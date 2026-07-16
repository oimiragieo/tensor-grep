"""Expands a single search word into related alternatives."""


def related_words(word, table):
    return table.get(word, [])
