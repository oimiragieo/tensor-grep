"""Moves a clock reading between two named offsets."""


def shift_clock(hour_value, from_offset, to_offset):
    return (hour_value - from_offset + to_offset) % 24
