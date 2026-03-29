from __future__ import annotations

import typing as t


class Choice:
    def __init__(self, choices: t.Sequence[str], case_sensitive: bool = True) -> None:
        self.choices = choices
        self.case_sensitive = case_sensitive

    def convert(self, value: t.Any, param: t.Any, ctx: t.Any) -> t.Any:
        normed_value = value
        normed_choices = {choice: choice for choice in self.choices}

        if ctx is not None and getattr(ctx, "token_normalize_func", None) is not None:
            normed_value = ctx.token_normalize_func(value)
            normed_choices = {
                ctx.token_normalize_func(normed_choice): original
                for normed_choice, original in normed_choices.items()
            }

        if not self.case_sensitive:
            normed_value = normed_value.casefold()
            normed_choices = {
                normed_choice.casefold(): original
                for normed_choice, original in normed_choices.items()
            }

        if normed_value in normed_choices:
            return normed_choices[normed_value]

        choices_str = ", ".join(map(repr, self.choices))
        raise ValueError(f"{value!r} is not one of {choices_str}.")
