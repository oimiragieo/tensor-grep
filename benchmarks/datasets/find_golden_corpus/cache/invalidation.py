"""Tells every process to drop a stale cache entry."""


def broadcast_cache_bust(key_name, subscriber_list):
    return [sub.notify(key_name) for sub in subscriber_list]
