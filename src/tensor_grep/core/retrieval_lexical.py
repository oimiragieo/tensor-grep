from __future__ import annotations

import re

_CAMEL_BOUNDARY_RE = re.compile(r"([a-z0-9])([A-Z])")
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def split_terms(text: str) -> list[str]:
    normalized = _CAMEL_BOUNDARY_RE.sub(r"\1 \2", text)
    normalized = normalized.replace("_", " ").replace("-", " ")
    return [token.lower() for token in _TOKEN_RE.findall(normalized) if token]


def score_term_overlap(query_terms: list[str], text: str) -> int:
    haystack_terms = set(split_terms(text))
    return sum(1 for term in query_terms if term in haystack_terms)
