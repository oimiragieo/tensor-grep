"""Pin flag parity between `blast-radius` and `blast-radius-render`.

WHY THIS EXISTS
---------------
An external agent dogfood (2026-08-22, v1.111.7) reported: `tg blast-radius --deadline` works,
`tg blast-radius-render --deadline` returns "No such option". Verified against the published
binary -- `blast-radius --help` contains `--deadline`, `blast-radius-render --help` does not.

The reporter's framing is the important part, and it is about AGENTS not humans:

    "Agents copying flags across siblings break."

A human reads the help text for the command they are running. An agent generalises: it learns
`--deadline` bounds a scan on one blast-radius command and applies it to the other, because
nothing in the payload or the naming says the surfaces differ. So this test pins the CLASS
(the two siblings share their scan-bounding vocabulary) rather than the one flag, because
fixing only `--deadline` leaves the next divergence to be found by the next agent.

WHY THESE FLAGS
---------------
Both commands take `path`, `symbol`, `provider`, `max_depth` and `max_repo_files` -- they are
the same scan with different output. A flag that bounds THE SCAN must therefore exist on both.
Flags that bound THE RENDER (`max_files`, `max_sources`, `max_symbols_per_file`) legitimately
exist only on the renderer, and this test must NOT demand those on `blast-radius`.

WHAT THIS DOES NOT CLAIM
------------------------
It does not assert the two flags behave identically at runtime, only that the surface exists on
both. A behavioural equivalence test would need a corpus and a timing arm; that is a separate
concern and is not smuggled in here.
"""

from __future__ import annotations

from typer.testing import CliRunner

from tensor_grep.cli.main import app

#: Flags that bound THE SCAN. Both siblings run the same scan, so both must accept these.
#: Render-only bounds (max_files, max_sources, max_symbols_per_file) are deliberately absent.
SCAN_BOUNDING_FLAGS = ("--max-depth", "--max-repo-files", "--deadline")

SIBLINGS = ("blast-radius", "blast-radius-render")


def _help_text(command: str) -> str:
    result = CliRunner().invoke(app, [command, "--help"])
    assert result.exit_code == 0, f"{command} --help exited {result.exit_code}\n{result.output}"
    return result.output


def test_positive_control_both_siblings_have_help() -> None:
    """Without this, a command that vanished would make every parity assertion vacuous."""
    for command in SIBLINGS:
        text = _help_text(command)
        assert len(text) > 200, f"{command} --help is implausibly short ({len(text)} chars)"
        assert "--max-depth" in text, (
            f"{command} --help lacks --max-depth, so it is not the command this test thinks it is"
        )


def test_detector_can_fail() -> None:
    """The matcher must be able to report absence, or the parity test proves nothing."""
    assert "--deadline" not in "usage: tg blast-radius-render [OPTIONS]", (
        "the substring matcher claims a flag is present in text that lacks it"
    )


def test_scan_bounding_flags_exist_on_both_blast_radius_siblings() -> None:
    missing: list[str] = []
    for command in SIBLINGS:
        text = _help_text(command)
        for flag in SCAN_BOUNDING_FLAGS:
            if flag not in text:
                missing.append(f"{command} is missing {flag}")

    assert not missing, (
        "blast-radius siblings disagree on scan-bounding flags, so an agent that learns a flag "
        "on one and applies it to the other gets 'No such option':\n  " + "\n  ".join(missing)
    )


def test_render_only_flags_are_not_demanded_of_the_plain_command() -> None:
    """Guard against over-correcting this parity rule into a false one.

    `blast-radius` renders nothing, so demanding the renderer's output-shaping flags on it would
    be a fabricated requirement. This test exists so a future 'make them fully identical' edit
    fails loudly instead of quietly adding dead options.
    """
    plain = _help_text("blast-radius")
    for render_only in ("--max-sources", "--max-symbols-per-file"):
        assert render_only not in plain, (
            f"blast-radius unexpectedly grew {render_only}; render-shaping flags belong to "
            "blast-radius-render only"
        )


def test_blast_radius_render_source_loop_overrun_stamps_partial_reason(tmp_path, monkeypatch):
    """An overrun on the RENDER path must say WHY, not just that it is partial.

    Deliberately NOT added to `_RENDER_FAMILY_DEADLINE_COMMAND_ARGS` in
    tests/unit/test_cli_deadline_coverage_gaps.py: that set is defined by the stage it patches
    (`build_context_pack_from_map`), which this command never touches. Adding it there fails for
    a STRUCTURAL reason -- the simulated overrun cannot fire -- which would look like a product
    defect and is not one. This test patches the stage `blast-radius-render` actually runs.

    The contract being pinned: `partial=true` with `partial_reason=None` tells an agent the
    result is incomplete but not why. `deadline_limit` alone is not equivalent -- a consumer
    that learned `partial_reason` on `tg prepare` reads None here and can only conclude
    "partial for an unknown reason". Every other deadline-bearing command stamps the reason.
    """
    import time as _time

    from tensor_grep.cli import repo_map

    (tmp_path / "helper.py").write_text("def helper(x):\n    return x\n", encoding="utf-8")
    (tmp_path / "caller.py").write_text(
        "from helper import helper\n\n\ndef use(y):\n    return helper(y)\n", encoding="utf-8"
    )

    original = repo_map.build_symbol_blast_radius_from_map

    calls: list[str] = []

    def _slow(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append("patched")
        # DETERMINISTIC, not racy: the sleep strictly exceeds the deadline below, so
        # `time.monotonic() >= deadline_monotonic` is guaranteed true by the time the source
        # loop performs its check. No wall-clock luck is involved.
        _time.sleep(0.4)
        return result

    monkeypatch.setattr(repo_map, "build_symbol_blast_radius_from_map", _slow)

    result = CliRunner().invoke(
        app,
        ["blast-radius-render", str(tmp_path), "helper", "--deadline", "0.3", "--json"],
    )
    import json as _json

    payload = _json.loads(result.stdout)

    # SETUP ASSERTIONS -- these run BEFORE the real one so a broken harness fails loudly
    # instead of quietly making the assertion below vacuous.
    #
    # An earlier revision of this test SKIPPED when the overrun did not fire, which a codex
    # audit correctly flagged HIGH: a test that can silently skip provides no coverage in the
    # environment where it skips, while still reporting success. Skipping is only honest when
    # the precondition is genuinely outside the test's control. Here it is not -- the test owns
    # the patch and the deadline, so a non-firing overrun means the HARNESS broke and must fail.
    assert calls, (
        "build_symbol_blast_radius_from_map was never called, so the deadline patch never ran -- "
        "this test would have asserted nothing. The render path likely changed shape."
    )
    assert payload.get("partial") is True, (
        "the forced overrun did not produce partial=true even though the patch slept 0.4s against "
        f"a 0.3s deadline. payload keys={sorted(payload)[:10]}"
    )

    assert payload.get("partial_reason") == "deadline", (
        "blast-radius-render reported partial=true without saying why. "
        f"partial_reason={payload.get('partial_reason')!r} "
        f"deadline_limit={payload.get('deadline_limit')!r}"
    )


def test_deadline_flag_actually_reaches_the_scan_not_just_the_help_text(tmp_path, monkeypatch):
    """A flag that parses but is dropped before the scan is WORSE than no flag.

    Closes a MED finding from the codex audit of this branch: the help-text parity test above
    passes as long as `--deadline` is DECLARED, so it would stay green if the option were
    accepted and then never threaded into the builder. Help text is a surface check; this is
    the wiring check.

    Asserts the value arrives at `build_symbol_blast_radius_render` as a `deadline_monotonic`
    stamp -- the parameter the underlying `_from_map` builder actually consumes.
    """
    from tensor_grep.cli import repo_map

    (tmp_path / "helper.py").write_text("def helper(x):\n    return x\n", encoding="utf-8")

    seen: dict[str, object] = {}
    original = repo_map.build_symbol_blast_radius_render

    def _capture(*args, **kwargs):
        seen["deadline_monotonic"] = kwargs.get("deadline_monotonic", "ABSENT")
        return original(*args, **kwargs)

    monkeypatch.setattr(repo_map, "build_symbol_blast_radius_render", _capture)

    CliRunner().invoke(
        app, ["blast-radius-render", str(tmp_path), "helper", "--deadline", "30", "--json"]
    )

    assert "deadline_monotonic" in seen, (
        "build_symbol_blast_radius_render was never called -- the wiring assertion below would "
        "be vacuous"
    )
    value = seen["deadline_monotonic"]
    assert value != "ABSENT", (
        "--deadline was accepted by the CLI but never passed to build_symbol_blast_radius_render; "
        "the flag parses and is then dropped"
    )
    assert isinstance(value, float), (
        f"deadline_monotonic reached the builder as {type(value).__name__} ({value!r}); it must be "
        "an absolute monotonic stamp produced by _deadline_monotonic_from_seconds"
    )


def test_wiring_check_control_no_flag_means_no_deadline(tmp_path, monkeypatch):
    """Control for the test above: WITHOUT the flag the builder must receive None.

    Without this, the wiring test would still pass if the code hardcoded a deadline for every
    run -- which would be a behaviour change on the default path, the exact thing the change
    promised not to do.
    """
    from tensor_grep.cli import repo_map

    (tmp_path / "helper.py").write_text("def helper(x):\n    return x\n", encoding="utf-8")

    seen: dict[str, object] = {}
    original = repo_map.build_symbol_blast_radius_render

    def _capture(*args, **kwargs):
        seen["deadline_monotonic"] = kwargs.get("deadline_monotonic", "ABSENT")
        return original(*args, **kwargs)

    monkeypatch.setattr(repo_map, "build_symbol_blast_radius_render", _capture)

    CliRunner().invoke(app, ["blast-radius-render", str(tmp_path), "helper", "--json"])

    assert seen.get("deadline_monotonic") is None, (
        "with no --deadline the builder must receive deadline_monotonic=None; got "
        f"{seen.get('deadline_monotonic')!r} -- the default path changed"
    )
