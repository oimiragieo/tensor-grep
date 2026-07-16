"""Confirms a message was not altered or truncated in transit."""

import zlib


class CorruptionFault(Exception):
    pass


def verify_checksum_or_reject(message_bytes, expected_crc):
    actual = zlib.crc32(message_bytes)
    if actual != expected_crc:
        raise CorruptionFault(f"crc mismatch: got {actual}, wanted {expected_crc}")
    return actual
