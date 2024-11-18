import frappe
from frappe import _
from frappe.email.doctype.notification.notification import (
    Notification,
    get_context,
    json,
)

from waba_integration.whatsapp_business_api_integration.doctype.waba_whatsapp_message.waba_whatsapp_message import (  # noqa
    WABAWhatsAppMessage,
)


class SendNotification(Notification):
    def validate(self):
        self.validate_twilio_settings()

    def validate_twilio_settings(self):
        if (
            self.enabled
            and self.channel == "WhatsApp"
            and not frappe.db.get_single_value("Twilio Settings", "enabled")
        ):
            frappe.throw(_("Please enable Twilio settings to send WhatsApp messages"))  # noqa

    def send(self, doc):
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
        receiver_list = self.get_receiver_list(doc, context)
        message = frappe.render_template(self.message, context)
        doctype = doc.doctype
        docname = doc.name

        for receiver in receiver_list:
            # Create new WABA Contact if not available
            if not frappe.db.exists("WABA WhatsApp Contact", {"name": receiver}):
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
