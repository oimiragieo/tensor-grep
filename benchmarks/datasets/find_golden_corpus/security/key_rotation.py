"""Replaces the active signing key on a schedule."""


def rotate_signing_key(current_key, key_generator_fn):
    return key_generator_fn(current_key)
