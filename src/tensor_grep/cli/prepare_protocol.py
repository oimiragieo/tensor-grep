"""Typed `ValidationAdvice` record for `tg prepare --include-next-action` (AGT-03 remainder,
docs/plans/2026-09-07-agentic-quality-simplification.md Task 03).

Why this module exists: `_select_validation_argv` decides, from the capsule's own detected
`validation_commands`, whether a reviewed argv is safe to hand back as executable advice (this
selector previously lived on `prepare_service.py` directly). That decision mixed two concerns in
one `tuple[str, ...] | None` value: the
*identity* of which recipe the repo-map detectors found (or that none matched), and the
*execution authority* to actually run something. Collapsing both into one optional tuple made it
easy for a future caller to treat "we found a recipe" and "you may run this" as the same fact.

`ValidationAdvice` splits them out explicitly:
  - `status` / `source` / `reason` describe what was DETECTED (identity) -- always safe to log,
    display, or reason about, even when nothing is runnable.
  - `argv` is the only field that carries EXECUTION AUTHORITY, and it is nonempty only when
    `status == "available"` for a recipe that matched the reviewed allowlist byte-for-byte.
  - `cwd` is carried alongside so a future consumer never has to guess where `argv` should run.

The allowlist and selector below are the single reviewed mapping from a detected command string
to trusted argv. `prepare_service.py` imports this module (never the reverse -- a module this
module's own docstring rule mirrors from `prepare_service`'s own "never import `cli.main` back"
rule) and keeps `_KNOWN_VALIDATION_RECIPES`/`_select_validation_argv` as aliases only for any
test still referencing the historical names on that module.
"""

from __future__ import annotations

from dataclasses import dataclass

_UNAVAILABLE_REASON = (
    "no allowlisted validation recipe detected for this repository "
    "-- inspect the change manually before treating it as verified"
)

# Exact-string allowlist of validation recipes the repo-map detectors themselves already emit
# (repo_map.py, repo_map_lang_js.py, repo_map_lang_rust.py). Deliberately NOT a shlex.split of
# arbitrary `validation_commands` text -- converting unreviewed shell strings into trusted argv
# is out; only a string matching one of these known-safe recipes byte-for-byte is ever turned
# into an executable argv, anything else falls back to `on_success.unavailable`.
_KNOWN_VALIDATION_RECIPES: dict[str, tuple[str, ...]] = {
    "pytest -q": ("pytest", "-q"),
    "pytest": ("pytest",),
    "uv run pytest -q": ("uv", "run", "pytest", "-q"),
    "uv run pytest": ("uv", "run", "pytest"),
    "cargo test": ("cargo", "test"),
    "go test ./...": ("go", "test", "./..."),
    "npm test": ("npm", "test"),
    "pnpm test": ("pnpm", "test"),
    "yarn test": ("yarn", "test"),
}


def _select_validation_argv(validation_commands: list[str]) -> tuple[str, ...] | None:
    """Pick a safe, reviewed argv for the first detected recipe that exactly matches the
    allowlist above, or ``None`` if nothing in ``validation_commands`` is recognized.

    Exact-match only: a string that merely starts with a known prefix (``"pytest -q; rm -rf /"``)
    is intentionally rejected rather than truncated or split, so an attacker cannot smuggle
    extra shell syntax past this gate by prefixing it with an innocuous-looking recipe.
    """
    for command in validation_commands:
        argv = _KNOWN_VALIDATION_RECIPES.get(command)
        if argv is not None:
            return argv
    return None


@dataclass(frozen=True)
class ValidationAdvice:
    """Typed advice about whether a reviewed, runnable validation command was detected.

    Attributes:
        status: ``"available"`` or ``"unavailable"``.
        argv: nonempty only when ``status == "available"`` for a reviewed, allowlisted recipe --
            this is the only field carrying execution authority.
        cwd: working directory ``argv`` should run in.
        source: the detected recipe's identity (the exact ``validation_commands`` string that
            matched), never execution authority by itself.
        reason: human-readable explanation, populated only when ``status == "unavailable"``.
    """

    status: str
    argv: tuple[str, ...]
    cwd: str
    source: str
    reason: str | None


def build_validation_advice(
    validation_commands: list[str],
    *,
    cwd: str = ".",
) -> ValidationAdvice:
    """Build a `ValidationAdvice` from detected `validation_commands`.

    Uses `_select_validation_argv` (the single reviewed allowlist above) -- this function never
    re-implements or duplicates that mapping.
    """
    matched_source = ""
    for command in validation_commands:
        if command in _KNOWN_VALIDATION_RECIPES:
            matched_source = command
            break

    argv = _select_validation_argv(validation_commands)
    if argv is not None:
        return ValidationAdvice(
            status="available",
            argv=argv,
            cwd=cwd,
            source=matched_source,
            reason=None,
        )
    return ValidationAdvice(
        status="unavailable",
        argv=(),
        cwd=cwd,
        source="",
        reason=_UNAVAILABLE_REASON,
    )
