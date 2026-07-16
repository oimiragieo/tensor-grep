"""Persists a point-in-time copy of the primary store."""


def take_snapshot(source_handle, destination_path):
    return source_handle.copy_to(destination_path)
