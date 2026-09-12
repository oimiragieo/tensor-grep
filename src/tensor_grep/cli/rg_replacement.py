"""Ripgrep `--replace` template expansion.

Split out of `cli/main.py` (2026-09-12) under the file-size ratchet. Pure string
transformation over an already-matched `re.Match`: no I/O, no CLI state, and no other
main.py-local dependency, which is why this was the safe thing to move rather than a
renderer that looked self-contained and was not.
"""

from __future__ import annotations

import re


def expand_ripgrep_replacement(template: str, match: re.Match[str]) -> str:
    def _is_ascii_digit(char: str) -> bool:
        return "0" <= char <= "9"

    def _is_ascii_ref_char(char: str) -> bool:
        return char == "_" or ("0" <= char <= "9") or ("A" <= char <= "Z") or ("a" <= char <= "z")

    def _resolve_token(token: str) -> str:
        if not token:
            return ""
        try:
            if all(_is_ascii_digit(char) for char in token):
                group_value = match.group(int(token))
            else:
                group_value = match.group(token)
        except Exception:
            return ""
        return "" if group_value is None else str(group_value)

    result: list[str] = []
    index = 0
    while index < len(template):
        char = template[index]
        if char != "$" or index + 1 >= len(template):
            result.append(char)
            index += 1
            continue

        next_char = template[index + 1]
        if next_char == "$":
            result.append("$")
            index += 2
            continue

        if next_char == "{":
            end_index = template.find("}", index + 2)
            if end_index != -1:
                result.append(_resolve_token(template[index + 2 : end_index]))
                index = end_index + 1
                continue

        if _is_ascii_ref_char(next_char):
            end_index = index + 2
            while end_index < len(template) and _is_ascii_ref_char(template[end_index]):
                end_index += 1
            result.append(_resolve_token(template[index + 1 : end_index]))
            index = end_index
            continue

        result.append("$")
        index += 1

    return "".join(result)
