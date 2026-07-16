"""Outbound short text alerts."""


def deliver_text_alert(phone_number, body):
    return {"phone": phone_number, "body": body[:160]}
