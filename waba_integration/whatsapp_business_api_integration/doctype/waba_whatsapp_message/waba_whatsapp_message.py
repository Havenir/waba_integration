# Copyright (c) 2022, Hussain Nagaria and contributors
# For license information, please see license.txt

import json
import mimetypes
from typing import Dict

import frappe
import requests
from frappe.model.document import Document
from frappe.utils import nowdate
from frappe.utils.safe_exec import get_safe_globals

MEDIA_TYPES = ("image", "sticker", "document", "audio", "video")


class WABAWhatsAppMessage(Document):
    def __init__(self, *args, **kwargs):
        """
        Initializes the WABA WhatsApp Message.

        The `access_token` is set to `None`, it is set when the access token is
        fetched from the WABA Settings.
        """
        super().__init__(*args, **kwargs)
        self._access_token = None

    def validate(self):
        """
        Validate that the WABA WhatsApp Message can be sent.

        This method checks if the WABA WhatsApp Message is enabled, the image attachment
        is valid, and sets the preview html if the message type is an audio or a video.
        """  # noqa
        settings = frappe.get_single("WABA Settings")
        if not settings.enabled:
            frappe.throw("WhatsApp Business API integration is not enabled.")

        self.validate_image_attachment()

        if self.message_type == "Audio" and self.media_file:
            self.preview_html = f"""
                <audio controls>
                    <source src="{self.media_file}" type="{self.media_mime_type}">
                    Your browser does not support the audio element.
                </audio>
            """  # noqa

        if self.message_type == "Video" and self.media_file:
            self.preview_html = f"""
                <video controls>
                    <source src="{self.media_file}" type="{self.media_mime_type}">
                    Your browser does not support the video element.
                </video>
            """  # noqa

        if not self.is_new():
            old_doc = self.get_doc_before_save()
            if not old_doc.attach_print and self.attach_print:
                self.generate_reference_pdf()

    def after_insert(self):
        """
        After insert hook.

        This method generates the reference pdf if the attach print is enabled.
        """
        if self.attach_print:
            self.generate_reference_pdf()

    def generate_reference_pdf(self):
        """
        Generate a reference PDF for the WABA WhatsApp Message.

        This method generates a PDF from the print format specified in the WABA WhatsApp Message
        and attaches it as a file to the WABA WhatsApp Message.

        The PDF is generated using the Standard print format if no print format is specified.
        The language of the PDF is set to the system's language.
        """  # noqa
        if self.attach_print:
            system_language = frappe.db.get_single_value(
                "System Settings", "language"
            )  # noqa
            pdf_print = frappe.attach_print(
                self.document_type,
                self.document_name,
                print_format=self.print_format or "Standard",
                lang=system_language,
            )

            pdf_attachment = frappe.get_doc(
                {
                    "doctype": "File",
                    "file_name": pdf_print["fname"],  # noqa
                    "is_private": 1,
                    "content": pdf_print["fcontent"],
                    "attached_to_doctype": self.doctype,
                    "attached_to_name": self.name,
                    "attached_to_field": "media_file",
                }
            )
            pdf_attachment.save(ignore_permissions=True)

            self.db_set("media_mime_type", "application/pdf")
            self.db_set("media_file", pdf_attachment.file_url)

            self.upload_media()

    def validate_image_attachment(self):
        """
        Validate the image attachment.

        This method ensures that the image attachment is properly set on the WABA WhatsApp Message.
        It sets the media_file to the media_image if media_image is set, and sets the media_image to the
        media_file if message_type is Image and media_file is set.
        """  # noqa

        if self.media_image:
            self.media_file = self.media_image

        if self.media_file and self.message_type == "Image":
            self.media_image = self.media_file

    @frappe.whitelist()
    def send(self) -> Dict:
        """
        Send the WABA WhatsApp Message.

        This method sends the WABA WhatsApp Message using the WhatsApp Business API.

        It validates that the WABA WhatsApp Message has a receipient set, and then sends the message
        to the specified receipient using the WhatsApp Business API.

        If the message type is Text, the message body is sent as a WhatsApp text message.
        If the message type is Audio, Image, Video, or Document, the media is sent as a WhatsApp
        media message.
        If the message type is Template, the template components are rendered and sent as a WhatsApp
        template message.

        Returns a dictionary containing the response from the WhatsApp Business API.
        """  # noqa
        if not self.to:
            frappe.throw("Recepient (`to`) is required to send message.")

        access_token = frappe.utils.password.get_decrypted_password(
            "WABA Settings", "WABA Settings", "access_token"
        )
        api_version = frappe.db.get_single_value(
            "WABA Settings", "api_version"
        )  # noqa
        api_base = f"https://graph.facebook.com/{api_version}"
        phone_number_id = frappe.db.get_single_value(
            "WABA Settings", "phone_number_id"
        )  # noqa

        endpoint = f"{api_base}/{phone_number_id}/messages"

        response_data = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": self.to,
            "type": self.message_type.lower(),
        }

        if self.message_type == "Text":
            response_data["text"] = {
                "preview_url": False,
                "body": self.message_body,
            }  # noqa

        if self.message_type in ("Audio", "Image", "Video", "Document"):
            if not self.media_id:
                frappe.throw(
                    "Please attach and upload the media before sending this message."  # noqa
                )

            response_data[self.message_type.lower()] = {
                "id": self.media_id,
            }

            if self.message_type == "Document":
                response_data[self.message_type.lower()][
                    "filename"
                ] = self.media_filename
                response_data[self.message_type.lower()][
                    "caption"
                ] = self.media_caption  # noqa

        if self.message_type == "Template":
            waba_template = frappe.get_doc(
                "WABA WhatsApp Message Template",
                self.message_template,
            )

            doc = frappe.get_doc(self.document_type, self.document_name)
            context = get_context(doc)
            context["message"] = self

            template_components = frappe.render_template(
                waba_template.components, context
            )
            template_components = json.loads(template_components)

            response_data["template"] = {
                "name": waba_template.name,
                "language": {"code": waba_template.language_code},
                "components": template_components,
            }

        response = requests.post(
            endpoint,
            json=response_data,
            headers={
                "Authorization": "Bearer " + access_token,
                "Content-Type": "application/json",
            },
        )

        if response.ok:
            self.id = response.json().get("messages")[0]["id"]
            self.status = "Sent"
            self.save(ignore_permissions=True)
            return response.json()
        else:
            frappe.throw(response.json().get("error").get("message"))

    @frappe.whitelist()
    def download_media(self) -> Dict:
        """
        Download media from WhatsApp Business API.

        This method downloads the media from WhatsApp Business API and saves it as a file
        in the file system. It sets the `media_file` field to the URL of the saved file
        and, if the message type is Image, sets the `media_image` field to the same URL
        which is used to display the image preview.

        Returns a dictionary with the file document.
        """  # noqa
        url = self.get_media_url()
        access_token = self.get_access_token()
        response = requests.get(
            url,
            headers={
                "Authorization": "Bearer " + access_token,
            },
        )

        file_name = get_media_extention(
            self, response.headers.get("Content-Type")
        )  # noqa
        file_doc = frappe.get_doc(
            {
                "doctype": "File",
                "file_name": file_name,
                "content": response.content,
                "attached_to_doctype": "WABA WhatsApp Message",
                "attached_to_name": self.name,
                "attached_to_field": "media_file",
                "is_private": True,
            }
        ).insert(ignore_permissions=True)

        self.set("media_file", file_doc.file_url)

        # Will be used to display image preview
        if self.message_type == "Image":
            self.set("media_image", file_doc.file_url)

        self.save()

        return file_doc.as_dict()

    def get_media_url(self) -> str:
        """
        Fetches the media URL for the given media ID.

        Returns the URL of the media or raises an exception if the request fails.
        """  # noqa
        if not self.media_id:
            frappe.throw("`media_id` is missing.")

        access_token = frappe.utils.password.get_decrypted_password(
            "WABA Settings", "WABA Settings", "access_token"
        )
        api_version = frappe.db.get_single_value(
            "WABA Settings", "api_version"
        )  # noqa
        api_base = f"https://graph.facebook.com/{api_version}"

        access_token = self.get_access_token()
        response = requests.get(
            f"{api_base}/{self.media_id}",
            headers={
                "Authorization": "Bearer " + access_token,
            },
        )

        if not response.ok:
            frappe.throw("Error fetching media URL")

        return response.json().get("url")

    def get_access_token(self) -> str:
        """
        Fetches the access token from the WABA Settings doctype.

        The access token is used to authenticate with the Facebook Graph API.
        It is stored as a password in the WABA Settings doctype and decrypted
        on the fly.

        Returns the decrypted access token.
        """
        if self._access_token is None:
            self._access_token = frappe.utils.password.get_decrypted_password(
                "WABA Settings", "WABA Settings", "access_token"
            )
        return self._access_token

    @frappe.whitelist()
    def upload_media(self):
        """
        Uploads the media file to the WhatsApp Business API.

        This method uploads the `media_file` to the WhatsApp Business API and
        stores the media ID in the `media_id` field.

        If the `media_mime_type` is not set, it is guessed using the Python
        `mimetypes` module.

        The method uses the `requests` library to send a multi-part form data
        to the WhatsApp Business API.

        If the upload is successful, the `media_uploaded` field is set to `True`
        and the `media_id` field is updated with the media ID returned by the
        API.

        If the upload fails, a `frappe.exceptions.ValidationError` is raised with
        the error message returned by the API.

        The `access_token` is fetched from the WABA Settings doctype.

        :param self: The instance of the `WABA WhatsApp Message` document.
        :type self: `Document`
        :raises frappe.exceptions.ValidationError: If the upload fails.
        """  # noqa
        if not self.media_file:
            frappe.throw("`media_file` is required to upload media.")

        media_file_path = frappe.get_doc(
            "File", {"file_url": self.media_file}
        ).get_full_path()
        access_token = self.get_access_token()
        access_token = frappe.utils.password.get_decrypted_password(
            "WABA Settings", "WABA Settings", "access_token"
        )
        api_version = frappe.db.get_single_value(
            "WABA Settings", "api_version"
        )  # noqa
        api_base = f"https://graph.facebook.com/{api_version}"

        phone_number_id = frappe.db.get_single_value(
            "WABA Settings", "phone_number_id"
        )  # noqa

        if not self.media_mime_type:
            self.media_mime_type = mimetypes.guess_type(self.media_file)[0]

        # Way to send multi-part form data
        # Ref: https://stackoverflow.com/a/35974071
        form_data = {
            "file": (
                "file",
                open(media_file_path, "rb"),
                self.media_mime_type,
            ),
            "messaging_product": (None, "whatsapp"),
            "type": (None, self.media_mime_type),
        }
        response = requests.post(
            f"{api_base}/{phone_number_id}/media",
            files=form_data,
            headers={
                "Authorization": "Bearer " + access_token,
            },
        )

        if response.ok:
            self.media_id = response.json().get("id")
            self.media_uploaded = True
            self.save(ignore_permissions=True)
        else:
            frappe.throw(response.json().get("error").get("message"))

    @frappe.whitelist()
    def mark_as_seen(self):
        """
        Marks an incoming message as seen.

        This method marks an incoming message as seen by making a POST request to
        the WhatsApp Business API with the `status` set to `read`.

        If the request is successful, the `status` field of the document is set to
        `Marked As Seen` and the document is saved.

        If the request fails, a `frappe.exceptions.ValidationError` is raised with
        the error message returned by the API.

        :param self: The instance of the `WABA WhatsApp Message` document.
        :type self: `Document`
        :raises frappe.exceptions.ValidationError: If the request fails.
        """  # noqa
        if self.type != "Incoming":
            frappe.throw("Only incoming messages can be marked as seen.")

        access_token = frappe.utils.password.get_decrypted_password(
            "WABA Settings", "WABA Settings", "access_token"
        )
        api_version = frappe.db.get_single_value(
            "WABA Settings", "api_version"
        )  # noqa
        api_base = f"https://graph.facebook.com/{api_version}"

        phone_number_id = frappe.db.get_single_value(
            "WABA Settings", "phone_number_id"
        )  # noqa
        endpoint = f"{api_base}/{phone_number_id}/messages"
        access_token = self.get_access_token()

        response = requests.post(
            endpoint,
            json={
                "messaging_product": "whatsapp",
                "status": "read",
                "message_id": self.id,
            },
            headers={
                "Authorization": "Bearer " + access_token,
                "Content-Type": "application/json",
            },
        )

        if response.ok:
            self.status = "Marked As Seen"
            self.save()
        else:
            frappe.throw(response.json().get("error").get("message"))


def get_context(doc):
    """
    Returns a context dictionary that can be used to render a template.

    The context includes the `doc` (WABA WhatsApp Message), `nowdate` (current date) and
    `frappe` (the `frappe` module as a dictionary).

    :param doc: The instance of the `WABA WhatsApp Message` document.
    :type doc: `Document`
    :return: A dictionary with the context.
    :rtype: `Dict`
    """  # noqa
    return {
        "doc": doc,
        "nowdate": nowdate,
        "frappe": get_safe_globals().get("frappe"),
    }  # noqa


def create_waba_whatsapp_message(message: Dict) -> WABAWhatsAppMessage:
    """
    Creates a WABA WhatsApp Message document based on the provided message data.

    This function processes incoming messages, creates a WhatsApp contact if it
    does not exist, and inserts a new WABA WhatsApp Message document. It supports
    text and media messages, including images and audio, and automatically downloads
    media if configured in WABA settings.

    :param message: A dictionary containing the message data with fields such as
                    'type', 'from', 'id', and content-specific fields for text or media.
    :type message: Dict
    :return: The created WABAWhatsAppMessage document.
    :rtype: WABAWhatsAppMessage
    :raises Exception: Logs an error if there is a problem downloading media.
    """  # noqa
    message_type = message.get("type")

    # Create whatsapp contact doc if not exists
    received_from = message.get("from")
    if not frappe.db.exists("WABA WhatsApp Contact", {"name": received_from}):
        frappe.get_doc(
            {
                "doctype": "WABA WhatsApp Contact",
                "whatsapp_id": received_from,
            }
        ).insert(ignore_permissions=True)

    message_data = frappe._dict(
        {
            "doctype": "WABA WhatsApp Message",
            "type": "Incoming",
            "status": "Received",
            "from": message.get("from"),
            "id": message.get("id"),
            "message_type": message_type.title(),
        }
    )

    if message_type == "text":
        message_data["message_body"] = message.get("text").get("body")
    elif message_type in MEDIA_TYPES:
        message_data["media_id"] = message.get(message_type).get("id")
        message_data["media_mime_type"] = message.get(message_type).get(
            "mime_type"
        )  # noqa
        message_data["media_hash"] = message.get(message_type).get("sha256")

    if message_type == "document":
        message_data["media_filename"] = message.get("document").get(
            "filename"
        )  # noqa
        message_data["media_caption"] = message.get("document").get("caption")

    message_doc = frappe.get_doc(message_data).insert(ignore_permissions=True)

    wants_automatic_image_downloads = frappe.db.get_single_value(
        "WABA Settings", "automatically_download_images"
    )

    wants_automatic_audio_downloads = frappe.db.get_single_value(
        "WABA Settings", "automatically_download_audio"
    )
    if (
        message_doc.message_type == "Image" and wants_automatic_image_downloads
    ) or (  # noqa
        message_doc.message_type == "Audio" and wants_automatic_audio_downloads
    ):
        try:
            current_user = frappe.session.user
            frappe.set_user("Administrator")
            message_doc.download_media()
            frappe.set_user(current_user)
        except Exception:
            frappe.log_error(
                f"WABA: Problem downloading {message_doc.message_type}",
                frappe.get_traceback(),
            )

    return message_doc


def process_status_update(status: Dict):
    """
    Updates the status of a WhatsApp message based on a status update received from WABA.

    :param status: Status update received from WABA
    :type status: dict
    """  # noqa
    message_id = status.get("id")
    status = status.get("status")

    frappe.db.set_value(
        "WABA WhatsApp Message", {"id": message_id}, "status", status.title()
    )


def get_media_extention(
    message_doc: WABAWhatsAppMessage, content_type: str
) -> str:  # noqa
    """
    Return the file extension of the media for a message.

    :param message_doc: The message document
    :type message_doc: WABAWhatsAppMessage
    :param content_type: The content type of the media
    :type content_type: str
    :return: The file extension of the media
    :rtype: str
    """

    return message_doc.media_filename or (
        "attachment_." + content_type.split(";")[0].split("/")[1]
    )
