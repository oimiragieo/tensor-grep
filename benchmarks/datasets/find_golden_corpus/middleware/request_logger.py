"""Records a short trace of each inbound call."""


def capture_access_trace(method, path, status_code):
    return f"{method} {path} -> {status_code}"
