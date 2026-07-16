"""Moves an urgent item ahead of the ordinary work list."""


def promote_urgent_message(pending_list, message_ref):
    if message_ref in pending_list:
        pending_list.remove(message_ref)
    pending_list.insert(0, message_ref)
    return pending_list
