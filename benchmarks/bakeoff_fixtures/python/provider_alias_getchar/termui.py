from _termui_impl import getchar as f

_getchar = f


def prompt(echo: bool) -> str:
    return _getchar(echo)
