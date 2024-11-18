import frappe


def boot_session(bootinfo):
    """Include twilio enabled flag into boot."""
    settings = frappe.get_single("WABA Settings")
    bootinfo.waba_enabled = settings.enabled
