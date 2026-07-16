"""Identifies which deployment tier the process is running in."""


def detect_deployment_stage(env_map):
    return env_map.get("APP_STAGE", "development")
