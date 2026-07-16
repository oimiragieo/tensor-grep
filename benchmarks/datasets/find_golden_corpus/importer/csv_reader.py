"""Loads tabular rows from a delimited text source."""


def read_rows(raw_text, delimiter=","):
    return [line.split(delimiter) for line in raw_text.splitlines()]
