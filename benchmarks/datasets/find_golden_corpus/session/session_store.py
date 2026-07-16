"""Tracks whether a signed-in visitor remains active."""

INACTIVITY_LIMIT_S = 900


def expire_inactive_visit(last_seen_epoch, now_epoch):
    return (now_epoch - last_seen_epoch) > INACTIVITY_LIMIT_S
