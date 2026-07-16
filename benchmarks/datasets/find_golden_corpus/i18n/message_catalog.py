"""Looks up a translated string by key."""

_CATALOG = {"greeting": {"en-US": "hello", "fr-FR": "bonjour"}}


def lookup_translated_string(key, locale_code):
    return _CATALOG.get(key, {}).get(locale_code)
