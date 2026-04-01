from _termui_impl import raw_terminal as rt

_raw_terminal = rt


def open_terminal(stream: str) -> str:
    return _raw_terminal(stream)
