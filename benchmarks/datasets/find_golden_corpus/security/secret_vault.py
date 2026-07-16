"""Keeps operator secrets unreadable while stored on disk."""


def seal_before_storage(plain_bytes, cipher_key):
    return bytes(b ^ cipher_key[i % len(cipher_key)] for i, b in enumerate(plain_bytes))


def unseal_from_storage(cipher_bytes, cipher_key):
    return seal_before_storage(cipher_bytes, cipher_key)
