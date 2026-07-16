"""Relays a call to a partner system and surfaces its outcome."""


class PartnerBreakdown(Exception):
    pass


def dispatch_outbound_call(endpoint, body):
    return {"endpoint": endpoint, "sent": body}


def raise_on_upstream_failure(status_code, response_body):
    if status_code >= 500:
        raise PartnerBreakdown(f"partner returned {status_code}: {response_body}")
    return response_body
