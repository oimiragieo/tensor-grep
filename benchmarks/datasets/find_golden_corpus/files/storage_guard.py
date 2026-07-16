"""Protects a write path from an exhausted volume."""


class VolumeExhaustedFault(Exception):
    pass


def reject_when_volume_full(capacity_probe_fn, needed_bytes):
    free_bytes = capacity_probe_fn()
    if free_bytes < needed_bytes:
        raise VolumeExhaustedFault(f"only {free_bytes} bytes free, need {needed_bytes}")
    return free_bytes
