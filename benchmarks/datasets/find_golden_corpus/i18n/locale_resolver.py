"""Chooses which translation catalog a response should use."""


def select_response_dialect(accept_header, available_catalogs):
    for tag in accept_header.split(","):
        code = tag.strip().split(";")[0]
        if code in available_catalogs:
            return code
    return available_catalogs[0]
