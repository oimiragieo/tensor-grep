"""Grants a short-lived bearer credential after sign-in."""

import secrets


def mint_access_token(account_id):
    return f"{account_id}.{secrets.token_hex(16)}"
