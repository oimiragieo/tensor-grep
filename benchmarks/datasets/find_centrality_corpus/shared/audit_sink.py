"""Appends structured lifecycle events to the shared audit log."""

_EVENTS = []


def record(event_name, subject_id):
    _EVENTS.append((event_name, subject_id))


def events_for(subject_id):
    return [event for event, subject in _EVENTS if subject == subject_id]


def clear():
    _EVENTS.clear()
