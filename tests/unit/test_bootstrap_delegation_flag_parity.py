"""HUNT-5: the argv fast-path door and the full-CLI door must refuse `-U`/`--multiline`/
`--multiline-dotall` for the SAME reason -- the flag routes to genuinely Python-only
functionality, not just "this one builder function forgot to forward a field."

`cli/main.py`'s `_can_delegate_to_native_tg_search(config, ...)` requires every field in
`_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS` to sit at its `SearchConfig` default before
delegating -- but that tuple protects `_build_native_tg_search_command`'s OWN argv
reconstruction from silently dropping a field it does not forward (verified: `after_context`/
`before_context` are in the tuple and ALSO absent from that function's forwarded-field set,
while a flag like `-A`/`-B` is a perfectly normal native `rg`-compatible flag with no Python-only
behavior at all). It is therefore NOT a list of "flags the native binary cannot support" --
an earlier draft of this test asserted the reverse and found ~90 false-positive "gaps" that are
not real defects. See `docs/BACKLOG.md`'s HUNT-5 entry for that investigation.

`cli/bootstrap.py`'s `_can_delegate_to_native_tg_search` is a SEPARATE, argv-string fast path
that passes RAW argv straight to the compiled native binary's own clap parser -- so `-A`/`-B`/
`--color` reach it directly and work correctly there. Its `unsupported_flags` set is instead
about flags whose FUNCTIONALITY has no native equivalent at all, and it has drifted out of
parity with the genuinely Python-only flags three times now: `-e`/`-f` (audit #69),
`--count-matches` (task #121), and `--multiline`/`-U`/`--multiline-dotall` (this session,
verified: `grep -n multiline src/tensor_grep/cli/bootstrap.py` returned zero hits while the
Python-side `--multiline` fail-closed gate, shipped this session as `5331dc9`, makes routing this
flag to a line-oriented native fallback a genuine correctness regression, not just a dropped
field).
"""

from __future__ import annotations

import ast
import inspect

from tensor_grep.cli import bootstrap as tg_bootstrap
from tensor_grep.cli import main as tg_main


def _search_command_flags_by_field() -> dict[str, list[str]]:
    """Map each `search_command` parameter name to its `typer.Option` flag strings, via AST --
    so the flag spelling used below can never silently drift from the real CLI declaration."""
    source = inspect.getsource(tg_main.search_command)
    tree = ast.parse(source)
    func_def = tree.body[0]
    assert isinstance(func_def, ast.FunctionDef), "search_command AST shape changed"

    result: dict[str, list[str]] = {}
    params = func_def.args.args
    defaults = func_def.args.defaults
    offset = len(params) - len(defaults)
    for i, param in enumerate(params):
        if i < offset:
            continue
        default_node = defaults[i - offset]
        if not (
            isinstance(default_node, ast.Call) and isinstance(default_node.func, ast.Attribute)
        ):
            continue
        if default_node.func.attr not in {"Option", "Argument"}:
            continue
        flags = [
            arg.value
            for arg in default_node.args
            if isinstance(arg, ast.Constant)
            and isinstance(arg.value, str)
            and arg.value.startswith("-")
        ]
        if flags:
            result[param.arg] = flags
    return result


class TestFlagDerivationMechanism:
    """Pins the AST-derivation mechanism itself against a KNOWN mapping, so a future
    `search_command` refactor that breaks the walk fails loudly here first, rather than the
    parity tests below silently checking the wrong (empty) flag list."""

    def test_multiline_field_maps_to_expected_flags(self) -> None:
        flags = _search_command_flags_by_field()
        assert set(flags.get("multiline", [])) == {"-U", "--multiline"}
        assert set(flags.get("multiline_dotall", [])) == {"--multiline-dotall"}


class TestBootstrapExcludesMultiline:
    """THE INVARIANT for THIS session's finding: multiline routes through
    `_TG_ONLY_SEARCH_FLAGS`/`SEARCH_PYTHON_PASSTHROUGH_FLAGS` on the full-CLI and native-Rust
    doors specifically because it needs the Python `--multiline` fail-closed gate (`5331dc9`) --
    bootstrap's separate argv fast path must refuse it too, or a `--json -U` search silently
    takes the fast path straight to the native binary's line-oriented fallback and answers a
    false "complete, no matches" instead of failing closed."""

    def test_multiline_short_flag_u_is_excluded(self) -> None:
        assert tg_bootstrap._can_delegate_to_native_tg_search(["--json", "-U"]) is False

    def test_multiline_long_flag_is_excluded(self) -> None:
        assert tg_bootstrap._can_delegate_to_native_tg_search(["--json", "--multiline"]) is False

    def test_multiline_dotall_flag_is_excluded(self) -> None:
        assert (
            tg_bootstrap._can_delegate_to_native_tg_search(["--json", "--multiline-dotall"])
            is False
        )

    def test_multiline_excluded_with_cpu_trigger_too(self) -> None:
        """The gate has multiple trigger flags (--cpu/--force-cpu/--json/--ndjson/
        --gpu-device-ids); pin a second one so the fix is not accidentally scoped to --json
        alone."""
        assert tg_bootstrap._can_delegate_to_native_tg_search(["--force-cpu", "-U"]) is False

    def test_plain_json_search_still_delegates(self) -> None:
        """Control: a search with NO excluded flag must still take the fast path, or this
        parity check is trivially satisfied by refusing everything."""
        assert tg_bootstrap._can_delegate_to_native_tg_search(["--json"]) is True

    def test_plain_context_flag_still_delegates(self) -> None:
        """Second control, the specific false-positive the first draft of this test produced:
        `-A`/context flags are real native rg flags with no Python-only behavior, and must NOT
        be excluded -- unlike `_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS`'s `after_context`
        entry, which governs a different function's argv-reconstruction completeness, not
        native-binary capability."""
        assert tg_bootstrap._can_delegate_to_native_tg_search(["--json", "-A", "3"]) is True
