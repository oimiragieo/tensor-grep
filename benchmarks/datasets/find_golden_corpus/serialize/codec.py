"""Packs and unpacks the internal binary wire frame."""


class FrameCorruptFault(Exception):
    pass


def pack_binary_frame(fields):
    return b"|".join(str(f).encode("ascii") for f in fields)


def unpack_binary_frame(raw_bytes):
    if b"|" not in raw_bytes:
        raise FrameCorruptFault("no field separator found")
    return raw_bytes.split(b"|")
