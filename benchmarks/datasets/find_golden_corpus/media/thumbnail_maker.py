"""Produces a small preview image from a source picture."""


def make_preview(source_image, max_dimension):
    return source_image.resize((max_dimension, max_dimension))
