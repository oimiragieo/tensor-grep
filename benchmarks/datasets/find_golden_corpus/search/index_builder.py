"""Prepares free-text content for later lookup."""


def build_inverted_index(documents):
    postings = {}
    for doc_id, text in documents.items():
        for term in set(text.lower().split()):
            postings.setdefault(term, set()).add(doc_id)
    return postings
