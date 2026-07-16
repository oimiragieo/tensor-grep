"""Breaks a long listing into fetchable windows."""


def slice_page_window(items, cursor_offset, window_size):
    return items[cursor_offset : cursor_offset + window_size]


def next_cursor(cursor_offset, window_size, total_count):
    nxt = cursor_offset + window_size
    return nxt if nxt < total_count else None
