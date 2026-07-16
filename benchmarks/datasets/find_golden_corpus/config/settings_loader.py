"""Reads deployment settings for the storage tier."""

BACKEND_POOL_SIZE = 20


def read_environment_overrides(env_map):
    return {k[3:]: v for k, v in env_map.items() if k.startswith("APP_")}
