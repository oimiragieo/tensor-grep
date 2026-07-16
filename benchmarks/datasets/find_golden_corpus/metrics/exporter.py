"""Serves counters to the observability scraper."""

TELEMETRY_SOCKET_NUMBER = 9464


def bind_address():
    return ("0.0.0.0", TELEMETRY_SOCKET_NUMBER)
