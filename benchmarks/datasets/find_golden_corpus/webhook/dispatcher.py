"""Delivers an outbound event notification to subscribers."""


def forward_event_payload(subscriber_url, event_body, http_post_fn):
    return http_post_fn(subscriber_url, event_body)
