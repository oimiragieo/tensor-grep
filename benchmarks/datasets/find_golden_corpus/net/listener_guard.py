"""Bounds how many pending sockets a listener will hold."""


class BacklogFullFault(Exception):
    pass


def reject_when_backlog_full(pending_count, backlog_ceiling):
    if pending_count >= backlog_ceiling:
        raise BacklogFullFault(f"{pending_count} pending >= ceiling {backlog_ceiling}")
    return pending_count + 1
