"""Assembles response markup from a named layout."""


def compose_html_fragment(layout_name, values):
    return f"<div data-layout='{layout_name}'>{values}</div>"
