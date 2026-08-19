"""DC-002: run BOTH JSON producers and diff them, instead of trusting shared fixtures.

THE GAP THIS CLOSES
-------------------
Two suites claim to guard the JSON envelope. `rust_core/tests/test_schema_compat.rs`
deserializes committed `docs/examples/*.json` with `deny_unknown_fields`.
`tests/unit/test_harness_api_docs.py` asserts the same files have the right shape.
Neither runs a producer.

So both engines are checked against the *same* hand-maintained fixtures, and nothing
compares them to each other. If both drift the same way, or the fixtures go stale,
every test stays green. Two methods that share an assumption are one method run
twice.

That is not hypothetical. Doing this diff by hand for five minutes found DC-003 --
one of eighteen Rust payload sites hardcoding the schema version while the other
seventeen used the constant. Neither existing suite could have seen it, because
none of the 36 committed fixtures carries the field involved.

WHY A SKIP HERE WOULD BE WORSE THAN NO TEST
-------------------------------------------
This needs a compiled native binary, which most environments lack, so the natural
shape is "skip if absent". A test that skips everywhere is a test that does not
exist -- and it reports as a passing suite, which is worse, because it is believed.

So absence of a binary is only tolerated when nobody promised one. Set
``TG_PARITY_REQUIRE=1`` and a missing binary becomes a hard FAILURE. The CI job that
builds the binary sets it; a laptop does not. The skip can therefore never quietly
become the permanent state in the one place that matters.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Envelope keys allowed to differ between the two producers, each with the reason.
#: This mapping is the contract. A key that diverges and is NOT listed here fails the
#: test -- which is what makes this fail-closed rather than a permissive smoke test.
DOCUMENTED_DIVERGENCES: dict[str, str] = {
    "routing_backend": "names the engine that served the query; differing IS the point",
    "routing_reason": "engine-specific routing explanation",
    "query": "native echoes the query; the Python envelope does not",
    "path": "native echoes the search root; the Python envelope does not",
    "schema_version": (
        "docs/CONTRACTS.md requires this on the doctor/ledger/MCP payloads, not on the "
        "plain search envelope. Python emitting it here is additive, and CONTRACTS.md "
        "s4 tells consumers to ignore unrecognized fields. Verified 2026-08-19."
    ),
    "routing_gpu_chunk_plan_mb": "Python-side GPU planning detail; absent from the native path",
    "routing_distributed": "Python-side distribution flag; absent from the native path",
    "routing_worker_count": "Python-side worker count; absent from the native path",
    "sidecar_used": "describes the Python sidecar; meaningless on the native path",
}

#: Keys whose VALUES must agree. These are the substance of the contract: a consumer
#: branching on any of them must get the same answer from either engine.
PARITY_KEYS = ("version", "total_files", "total_matches")


def _is_executable(candidate: Path) -> bool:
    """Probe `--version` rather than trusting `is_file()`.

    A Git Bash path such as `/c/Users/.../tg` looks like a file to some tooling but
    is a shell shim Windows Python cannot exec -- it dies with a bare
    `OSError: [WinError 193] %1 is not a valid Win32 application` from deep inside
    subprocess, which reads like a broken test rather than a bad path. Measured
    2026-08-19. Three path namespaces exist on this box and they are not
    interchangeable; the only reliable check is to run the thing.
    """
    try:
        proc = subprocess.run(
            [str(candidate), "--version"], capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def _native_binary() -> Path | None:
    """Resolve the native binary to an ABSOLUTE path.

    Absolute matters: both producers are invoked with `cwd=<corpus>` (a tmp dir),
    so a relative binary path resolves against the corpus rather than the repo and
    dies with FileNotFoundError. That is not hypothetical -- CI passed
    `rust_core/target/release/tg`, `_is_executable` probed it with no `cwd` and
    succeeded from the workspace root, and the real call then failed from the
    corpus dir. The probe validated under different conditions than the call it
    was vouching for, which made a correct binary look absent.
    """
    override = os.environ.get("TG_PARITY_NATIVE_BINARY")
    if override:
        candidate = Path(override).expanduser()
        if not candidate.is_absolute():
            candidate = (REPO_ROOT / candidate).resolve()
        return candidate if _is_executable(candidate) else None
    try:
        sys.path.insert(0, str(REPO_ROOT / "src"))
        from tensor_grep.cli.runtime_paths import resolve_native_tg_binary

        resolved = resolve_native_tg_binary()
        if resolved is not None:
            return Path(resolved).resolve()
    except Exception:
        pass
    found = shutil.which("tg")
    return Path(found).resolve() if found else None


def _require_binary() -> Path:
    binary = _native_binary()
    if binary is not None:
        return binary
    if os.environ.get("TG_PARITY_REQUIRE") == "1":
        pytest.fail(
            "TG_PARITY_REQUIRE=1 but no native tg binary was found. The job that sets "
            "this variable is the one that builds the binary, so this is a wiring "
            "failure, not a missing optional dependency. Set "
            "TG_PARITY_NATIVE_BINARY=<path> or fix the build step."
        )
    pytest.skip(
        "no native tg binary available; set TG_PARITY_NATIVE_BINARY to run the "
        "producer-parity diff (TG_PARITY_REQUIRE=1 makes its absence a failure)"
    )


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    """A tiny, fully-deterministic corpus. Both producers see identical bytes."""
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "alpha.py").write_text(
        "def alpha():\n    return 1\n\n\ndef beta():\n    return alpha()\n",
        encoding="utf-8",
    )
    (root / "gamma.py").write_text(
        "from alpha import alpha\n\n\ndef delta():\n    return alpha() + 1\n",
        encoding="utf-8",
    )
    return root


def _run(argv: list[str], cwd: Path) -> tuple[int, str]:
    proc = subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=180,
    )
    return proc.returncode, proc.stdout


def _native_envelope(binary: Path, corpus_dir: Path) -> dict:
    code, out = _run([str(binary), "search", "alpha", ".", "--json"], corpus_dir)
    assert code in (0, 1), f"native exited {code}: {out[:400]}"
    return json.loads(out)


def _python_envelope(corpus_dir: Path) -> dict:
    env_python = [sys.executable, "-m", "tensor_grep", "search", "alpha", ".", "--json"]
    code, out = _run(env_python, corpus_dir)
    assert code in (0, 1), f"python exited {code}: {out[:400]}"
    return json.loads(out)


def test_relative_binary_override_is_resolved_against_the_repo_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression control for the CI failure this test caused on its first run.

    CI sets TG_PARITY_NATIVE_BINARY=rust_core/target/release/tg -- a RELATIVE path.
    Both producers are invoked with `cwd=<corpus>`, so a relative path resolves
    against the corpus and the binary vanishes. `_is_executable` did not catch it,
    because it probed with no `cwd` and therefore succeeded from the workspace
    root: the probe passed under conditions the real call never runs under.

    Asserting on `_native_binary`'s OUTPUT rather than on subprocess behaviour, so
    this control runs on any machine.
    """
    monkeypatch.setenv("TG_PARITY_NATIVE_BINARY", "some/relative/path/tg")
    monkeypatch.setattr(
        sys.modules[__name__], "_is_executable", lambda _candidate: True, raising=True
    )
    resolved = _native_binary()
    assert resolved is not None
    assert resolved.is_absolute(), f"{resolved} is not absolute; subprocess cwd would break it"
    assert str(REPO_ROOT) in str(resolved), "relative override must anchor to the repo root"


def test_documented_divergences_are_all_explained() -> None:
    """Every exemption carries a reason. An unexplained entry is how an allowlist
    silently becomes a dumping ground -- the same failure the file-size ratchet's
    retire-the-exception rule guards against."""
    for key, reason in DOCUMENTED_DIVERGENCES.items():
        assert reason and len(reason) > 20, f"{key} has no real justification"


def test_parity_keys_and_divergences_are_disjoint() -> None:
    """A key cannot be both required-to-match and allowed-to-differ.

    Without this, adding a troublesome key to the divergence list would silently
    remove it from the parity set and the test would still pass.
    """
    overlap = set(PARITY_KEYS) & set(DOCUMENTED_DIVERGENCES)
    assert not overlap, f"{overlap} is both required to match and allowed to differ"


def test_python_producer_emits_the_parity_keys(corpus: Path) -> None:
    """Positive control for the Python arm, and it runs everywhere.

    If the Python envelope ever stops carrying these, the parity test below would
    pass vacuously on a shape with nothing left to compare.
    """
    envelope = _python_envelope(corpus)
    for key in PARITY_KEYS:
        assert key in envelope, f"{key} missing from the Python envelope: {sorted(envelope)}"
    assert envelope["total_matches"] > 0, "corpus produced no matches; the fixture is broken"


def test_producers_agree_on_the_shared_contract(corpus: Path) -> None:
    """THE test. Both engines, same input, same invocation -- diff the envelopes."""
    binary = _require_binary()
    native = _native_envelope(binary, corpus)
    python = _python_envelope(corpus)

    # 1. Values that must agree.
    mismatches = [
        f"  {key}: native={native.get(key)!r} python={python.get(key)!r}"
        for key in PARITY_KEYS
        if native.get(key) != python.get(key)
    ]
    assert not mismatches, "producers disagree on contract values:\n" + "\n".join(mismatches)

    # 2. Any key present in one and not the other must be a DOCUMENTED divergence.
    #    This is the fail-closed half: a newly-added field on one side only will fail
    #    here until somebody writes down why, which is exactly the review DC-002 wants.
    only_native = set(native) - set(python) - set(DOCUMENTED_DIVERGENCES)
    only_python = set(python) - set(native) - set(DOCUMENTED_DIVERGENCES)
    assert not (only_native or only_python), (
        "undocumented envelope divergence between producers.\n"
        f"  native-only: {sorted(only_native)}\n"
        f"  python-only: {sorted(only_python)}\n"
        "Either make the two agree, or add the key to DOCUMENTED_DIVERGENCES with a "
        "reason citing docs/CONTRACTS.md."
    )

    # 3. The match SET must agree (by basename, since invocation controls path spelling).
    def match_set(envelope: dict) -> set[tuple[str, int, str]]:
        return {
            (Path(str(m["file"]).replace("\\", "/")).name, int(m["line"]), str(m["text"]).strip())
            for m in envelope.get("matches", [])
        }

    native_matches, python_matches = match_set(native), match_set(python)
    assert native_matches, "native returned no matches; the comparison would be vacuous"
    assert native_matches == python_matches, (
        "producers disagree on the match set:\n"
        f"  native-only: {sorted(native_matches - python_matches)}\n"
        f"  python-only: {sorted(python_matches - native_matches)}"
    )
