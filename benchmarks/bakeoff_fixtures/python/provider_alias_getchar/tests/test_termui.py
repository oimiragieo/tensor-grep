from termui import prompt


def test_prompt_wrapper() -> None:
    assert prompt(True) == "y"
