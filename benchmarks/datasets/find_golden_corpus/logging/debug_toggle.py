"""Controls verbose diagnostic output at process start."""

VERBOSE_TRACE_ENV_VAR = "APP_VERBOSE_TRACE"


def verbose_mode_requested(env_map):
    return env_map.get(VERBOSE_TRACE_ENV_VAR, "") == "1"
