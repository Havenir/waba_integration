import frappe

from frappe.email.doctype.notification.notification import (  # noqa  # isort:skip
    Notification,
    get_context,
    json,
)

from waba_integration.whatsapp_business_api_integration.doctype.waba_whatsapp_message.waba_whatsapp_message import (  # noqa  # isort:skip
    WABAWhatsAppMessage,
)


class SendNotification(Notification):
    def send(self, doc):
        """
        Override the send method of the Notification class to add the WhatsApp BA channel.
        If the channel is set to "WhatsApp BA", it will call the send_whatsapp_msg method.
        """  # noqa
        context = get_context(doc)
        context = {"doc": doc, "alert": self, "comments": None}
        if doc.get("_comments"):
            context["comments"] = json.loads(doc.get("_comments"))

        if self.is_standard:
            self.load_standard_properties(context)

        try:
            if self.channel == "WhatsApp BA":
                self.send_whatsapp_msg(doc, context)
        except Exception:
            frappe.log_error(
                title="Failed to send notification",
                message=frappe.get_traceback(),  # noqa
            )

        super(SendNotification, self).send(doc)

    def send_whatsapp_msg(self, doc, context):
        """
        Sends a WhatsApp message via the WhatsApp Business API.

        This method retrieves a list of receivers and sends a message to each receiver.
        It checks if the receiver exists as a WABA WhatsApp Contact and creates one if necessary.
        It then creates a WABA WhatsApp Message document with the message details and sends it.

        :param doc: The document triggering the notification.
        :param context: The context used for rendering the message template.
        """  # noqa
        receiver_list = self.get_receiver_list(doc, context)
        message = frappe.render_template(self.message, context)
        doctype = doc.doctype
        docname = doc.name

        for receiver in receiver_list:
            # Create new WABA Contact if not available
            if not frappe.db.exists(
                "WABA WhatsApp Contact", {"name": receiver}
            ):  # noqa
                frappe.get_doc(
                    {
                        "doctype": "WABA WhatsApp Contact",
                        "whatsapp_id": receiver,
                        "display_name": frappe.db.get_value(
                            "User", {"mobile_no": receiver}, "full_name"
                        ),
                    }
                ).insert(ignore_permissions=True)

            wa_message: WABAWhatsAppMessage = frappe.get_doc(
                {
                    "doctype": "WABA WhatsApp Message",
                    "to": receiver,
                    "message_body": message,
                    "document_type": doctype,
                    "document_name": docname,
                    "attach_print": self.attach_print,
                    "print_format": self.print_format,
                    "message_type": "Template"
                    if self.waba_whatsapp_message_template
                    else "Text",
                    "message_template": self.waba_whatsapp_message_template,
                }
            )

            wa_message.insert(ignore_permissions=True)
            wa_message.send()
