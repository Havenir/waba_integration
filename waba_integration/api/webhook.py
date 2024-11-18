import frappe
from waba_integration.whatsapp_business_api_integration.doctype.waba_whatsapp_message.waba_whatsapp_message import (  # noqa
    create_waba_whatsapp_message,
    process_status_update,
)

from werkzeug.wrappers import Response


@frappe.whitelist(allow_guest=True)
def handle():
    if frappe.request.method == "GET":
        return verify_token_and_fulfill_challenge()

    try:
        form_dict = frappe.local.form_dict
        messages = form_dict["entry"][0]["changes"][0]["value"].get("messages", [])  # noqa
        statuses = form_dict["entry"][0]["changes"][0]["value"].get("statuses", [])  # noqa

        for status in statuses:
            process_status_update(status)

        for message in messages:
            create_waba_whatsapp_message(message)

        frappe.get_doc(
            {"doctype": "WABA Webhook Log", "payload": frappe.as_json(form_dict)}  # noqa
        ).insert(ignore_permissions=True)
    except Exception:
        message = frappe.get_traceback()
        frappe.log_error(title="WABA Webhook Log Error", message=message)

        form_dict = frappe.local.form_dict
        frappe.get_doc(
            {"doctype": "WABA Webhook Log", "payload": frappe.as_json(form_dict)}  # noqa
        ).insert(ignore_permissions=True)


def verify_token_and_fulfill_challenge():
    meta_challenge = frappe.form_dict.get("hub.challenge")
    expected_token = frappe.db.get_single_value("WABA Settings", "webhook_verify_token")  # noqa

    if frappe.form_dict.get("hub.verify_token") != expected_token:
        frappe.throw("Verify token does not match")

    return Response(meta_challenge, status=200)
