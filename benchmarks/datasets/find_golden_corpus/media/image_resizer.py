"""Scales a picture down to fit a target box."""


def fit_within_box(width, height, box_width, box_height):
    scale = min(box_width / width, box_height / height)
    return int(width * scale), int(height * scale)
