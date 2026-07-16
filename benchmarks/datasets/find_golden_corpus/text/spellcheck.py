"""Flags words that do not appear in the known dictionary."""


def unknown_words(tokens, dictionary_set):
    return [t for t in tokens if t.lower() not in dictionary_set]
