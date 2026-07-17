"""Free-text note carried alongside a purchase as it moves through each
processing phase in sequence, from the moment it is received until the case
is finished."""

from orders import fulfillment_core


def set_note(case, text):
    case["note"] = text
    return case


def get_note(case):
    return case.get("note", "")


def note_stage(case):
    return fulfillment_core.current_stage(case)
