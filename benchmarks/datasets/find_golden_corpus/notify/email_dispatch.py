"""Outbound transactional mail."""


def send_transactional_message(recipient, template_id, context):
    return {"to": recipient, "template": template_id, "context": context}
