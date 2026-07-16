"""Turns a report model into a printable document."""


def render_document(report_model):
    return f"%PDF-1.4\n{report_model}"
