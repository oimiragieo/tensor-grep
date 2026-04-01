from termui import open_terminal


def test_open_terminal_wrapper() -> None:
    assert open_terminal("stdin") == "raw:stdin"
