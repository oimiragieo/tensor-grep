"""MCP ruleset listing and ruleset-scan contracts."""

import hashlib
import json
from pathlib import Path


def test_tg_rulesets_returns_builtin_ruleset_metadata():
    from tensor_grep.cli import mcp_server

    payload = json.loads(mcp_server.tg_rulesets())
    assert payload["routing_reason"] == "builtin-rulesets"
    rulesets = {ruleset["name"]: ruleset for ruleset in payload["rulesets"]}
    assert set(rulesets) == {
        "auth-safe",
        "crypto-safe",
        "deserialization-safe",
        "secrets-basic",
        "subprocess-safe",
        "tls-safe",
    }
    assert rulesets["auth-safe"]["category"] == "security"
    assert "python" in rulesets["auth-safe"]["languages"]


def test_tg_ruleset_scan_returns_structured_findings(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server
    from tests.unit.test_cli_modes_shared import _FakeAstPipeline, _FakeAstScanner

    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)
    monkeypatch.chdir(tmp_path)

    Path("a.py").write_text("hashlib.md5($$$ARGS)\n", encoding="utf-8")
    Path("b.py").write_text("ok\n", encoding="utf-8")

    payload = json.loads(mcp_server.tg_ruleset_scan("crypto-safe", path=".", language="python"))

    assert payload["routing_reason"] == "builtin-ruleset-scan"
    assert payload["ruleset"] == "crypto-safe"
    assert payload["rule_count"] == 2
    assert payload["matched_rules"] == 1
    assert payload["total_matches"] == 1
    assert payload["findings"][0]["rule_id"] == "python-hashlib-md5"
    assert payload["findings"][0]["severity"] == "high"
    assert "hashlib.md5" in payload["findings"][0]["message"]
    assert (
        payload["findings"][0]["fingerprint"]
        == hashlib.sha256(
            json.dumps(
                {
                    "rule_id": "python-hashlib-md5",
                    "language": "python",
                    "files": ["a.py"],
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
    )
    assert payload["findings"][0]["files"] == ["a.py"]
    assert payload["findings"][0]["evidence"] == [{"file": "a.py", "match_count": 1}]


# audit #95 Part 2 [SEC]: `inline_rules` on tg_ruleset_scan -- the `--inline-rules` CLI source
# (a string of ast-grep rule YAML, ZERO file I/O), mirrored via _load_inline_rule_specs (never
# reimplemented). `ruleset` becomes optional; exactly one of ruleset/inline_rules is required.


def test_tg_ruleset_scan_supports_inline_rules(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server
    from tests.unit.test_cli_modes_shared import _FakeAstPipeline, _FakeAstScanner

    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)
    monkeypatch.chdir(tmp_path)

    Path("a.py").write_text("print($A)\n", encoding="utf-8")
    Path("b.py").write_text("ok\n", encoding="utf-8")

    inline_rules = "\n".join([
        "id: no-print",
        "language: python",
        "rule:",
        "  pattern: print($A)",
    ])

    payload = json.loads(mcp_server.tg_ruleset_scan(inline_rules=inline_rules, path="."))

    assert payload["routing_reason"] == "ast-inline-rules-scan"
    assert payload["config_path"] == "inline-rules"
    assert payload["ruleset"] is None
    assert payload["rule_count"] == 1
    assert payload["matched_rules"] == 1
    assert payload["findings"][0]["rule_id"] == "no-print"
    assert payload["findings"][0]["files"] == ["a.py"]


def test_tg_ruleset_scan_inline_rules_preserves_severity_and_message(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server
    from tests.unit.test_cli_modes_shared import _FakeAstPipeline, _FakeAstScanner

    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)
    monkeypatch.chdir(tmp_path)

    Path("a.py").write_text("print($A)\n", encoding="utf-8")

    inline_rules = "\n".join([
        "id: no-print",
        "language: python",
        "severity: warning",
        "message: Avoid print in library code.",
        "rule:",
        "  pattern: print($A)",
    ])

    payload = json.loads(mcp_server.tg_ruleset_scan(inline_rules=inline_rules, path="."))

    finding = payload["findings"][0]
    assert finding["rule_id"] == "no-print"
    assert finding["severity"] == "warning"
    assert finding["message"] == "Avoid print in library code."


def test_tg_ruleset_scan_ruleset_and_inline_rules_are_mutually_exclusive(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    payload = json.loads(
        mcp_server.tg_ruleset_scan(
            ruleset="crypto-safe",
            inline_rules="id: x\nrule:\n  pattern: y\n",
            path=".",
        )
    )

    assert payload["error"]["code"] == "invalid_input"
    assert "mutually exclusive" in payload["error"]["message"]


def test_tg_ruleset_scan_requires_ruleset_or_inline_rules(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    payload = json.loads(mcp_server.tg_ruleset_scan(path="."))

    assert payload["error"]["code"] == "invalid_input"
    assert "one of ruleset or inline_rules" in payload["error"]["message"].lower()


def test_tg_ruleset_scan_inline_rules_invalid_yaml_fails_closed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    payload = json.loads(mcp_server.tg_ruleset_scan(inline_rules="id: broken\nrule: [", path="."))

    assert payload["error"]["code"] == "invalid_input"
    assert "YAML" in payload["error"]["message"]
    assert "Traceback" not in payload["error"]["message"]


def test_tg_ruleset_scan_inline_rules_no_valid_rules_fails_closed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    # Valid YAML, but no `rule.pattern`/`pattern` field anywhere -- _load_inline_rule_specs
    # extracts zero specs from this document.
    payload = json.loads(mcp_server.tg_ruleset_scan(inline_rules="id: no-pattern-here\n", path="."))

    assert payload["error"]["code"] == "invalid_input"
    assert "no valid inline rules" in payload["error"]["message"].lower()


def test_tg_ruleset_scan_inline_rules_unsupported_language_fails_closed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    inline_rules = "\n".join([
        "id: unsupported-language",
        "language: Dart",
        "rule:",
        "  pattern: print($A)",
    ])

    payload = json.loads(mcp_server.tg_ruleset_scan(inline_rules=inline_rules, path="."))

    assert payload["error"]["code"] == "invalid_input"
    assert "Unsupported AST language Dart" in payload["error"]["message"]
    assert "Traceback" not in payload["error"]["message"]


def test_tg_ruleset_scan_inline_rules_honors_explicit_language_override(monkeypatch, tmp_path):
    """The `inferred_language = normalize_ast_language(language) if language else
    str(rules[0]["language"]) else` branch only runs when the caller passes `language=`
    explicitly (the rule's OWN embedded `language:` field, and the no-override default, take
    a different path entirely) -- exercise it directly so a regression there (e.g. a missing
    normalize_ast_language import) is actually caught."""
    from tensor_grep.cli import mcp_server
    from tests.unit.test_cli_modes_shared import _FakeAstPipeline, _FakeAstScanner

    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)
    monkeypatch.chdir(tmp_path)

    Path("a.py").write_text("print($A)\n", encoding="utf-8")

    # No `language:` field on the rule itself -- the explicit `language=` override must supply
    # it via the `if language:` branch, not the rule's own per-document field.
    inline_rules = "id: no-print\nrule:\n  pattern: print($A)\n"

    payload = json.loads(
        mcp_server.tg_ruleset_scan(inline_rules=inline_rules, path=".", language="python")
    )

    assert payload["language"] == "python"
    assert payload["findings"][0]["rule_id"] == "no-print"


def test_tg_ruleset_scan_inline_rules_rejects_oversized_input(tmp_path, monkeypatch):
    """[SEC] YAML expansion-bomb DoS -- DEFENSE-IN-DEPTH layer 1 (the length cap). Bounding the
    raw string length before it reaches the YAML loader is a cheap, unconditional guard --
    verify the tool actually enforces the cap (not merely documents it) and that the rejection
    happens BEFORE any YAML parsing (a bomb payload well past the cap must still be refused
    fast, not hang trying to parse it). NOTE: the length cap ALONE does NOT stop the bomb -- an
    aliased payload detonates by depth ~9 while the cap admits depth ~1000, so the real fix is
    the loader-level alias rejection; see test_tg_ruleset_scan_inline_rules_rejects_yaml_alias_bomb
    (audit #95 Part-2 Opus-gate BLOCK)."""
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    oversized = "a" * (mcp_server._MAX_INLINE_RULES_CHARS + 1)

    payload = json.loads(mcp_server.tg_ruleset_scan(inline_rules=oversized, path="."))

    assert payload["error"]["code"] == "invalid_input"
    assert "exceeds" in payload["error"]["message"].lower()
    assert str(mcp_server._MAX_INLINE_RULES_CHARS) in payload["error"]["message"]


def test_tg_ruleset_scan_inline_rules_rejects_yaml_alias_bomb(tmp_path, monkeypatch):
    """[SEC] YAML alias-expansion DoS (billion-laughs) -- audit #95 Part-2 Opus-gate BLOCK.

    SafeLoader SHARES alias nodes, so the load itself is linear -- but the downstream ``str()``
    coercions on ``id``/``severity``/``message`` in ``_load_inline_rule_specs`` deep-walk that
    shared graph and expand it ~9^depth. A sub-64 KiB nested-alias payload therefore detonates by
    depth ~9 (the gate proved a 469-byte payload hung >15s), completely under the length cap
    (which admits depth ~1000). The fix rejects YAML aliases at the loader level
    (``_NoAliasSafeLoader.compose_node`` raises on the first ``AliasEvent``, before any expansion),
    so the bomb is refused as ``invalid_input`` fast.

    Kept SHALLOW (depth 5) on purpose: the fix rejects at the FIRST alias regardless of depth, so
    shallow still proves it, and if the loader-level rejection ever regresses this test FAILS FAST
    on the assertion (the scan returns a non-``invalid_input`` result) instead of OOM/hanging the
    suite. A watchdog thread is the belt-and-suspenders anti-hang guard (anti-hang-test-protocol).
    """
    import threading

    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)

    # Nested YAML aliases -- the billion-laughs vector. a0 is the anchored seed; each level
    # aliases the previous 9x. WELL under _MAX_INLINE_RULES_CHARS, so ONLY the _NoAliasSafeLoader
    # alias rejection -- not the length bound -- can stop it. The `severity: *a4` reaches the
    # str()-coercion detonation point.
    lines = ['a0: &a0 ["lol", "lol", "lol", "lol", "lol", "lol", "lol", "lol", "lol"]']
    for depth in range(1, 5):
        refs = ", ".join([f"*a{depth - 1}"] * 9)
        lines.append(f"a{depth}: &a{depth} [{refs}]")
    lines += ["rules:", '  - pattern: "print($X)"', "    severity: *a4"]
    bomb = "\n".join(lines)
    assert len(bomb) < mcp_server._MAX_INLINE_RULES_CHARS, (
        "payload must be sub-cap to test the loader, not the length bound"
    )

    result: dict[str, str] = {}

    def _run() -> None:
        result["payload"] = mcp_server.tg_ruleset_scan(inline_rules=bomb, path=".")

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    worker.join(timeout=15.0)
    assert not worker.is_alive(), (
        "tg_ruleset_scan HUNG on a sub-64 KiB YAML alias bomb -- the _NoAliasSafeLoader alias "
        "rejection regressed; the _MAX_INLINE_RULES_CHARS length cap alone does NOT stop this DoS."
    )

    payload = json.loads(result["payload"])
    assert payload.get("error", {}).get("code") == "invalid_input", (
        f"alias bomb must be refused as invalid_input (loader-level alias rejection); got: {payload}"
    )
    assert "YAML" in payload["error"]["message"]
    assert "Traceback" not in payload["error"]["message"]


def test_tg_ruleset_scan_inline_rules_rejects_deep_nested_yaml(tmp_path, monkeypatch):
    """[SEC] Deep-nested ALIAS-FREE YAML DoS residual -- audit #95 Part-2 re-gate BLOCK.

    A ~40 KB payload of 20000 nested flow-sequences (`"["*20000 + "]"*20000`) is UNDER the 64 KiB
    length cap and has NO aliases, so `_NoAliasSafeLoader` cannot reject it -- but it recurses the
    YAML parser/composer past the interpreter's recursion limit. The pure-Python SafeLoader raises
    a CATCHABLE `RecursionError` (the old CSafeLoader hard-crashed the whole process, exit
    0xC00000FD); the fix catches `RecursionError` at the load site so this path also fails closed
    as a structured `invalid_input` instead of escaping as a raw traceback (the tool's fail-closed
    contract). Fast (<1s, O(input) memory, process survives). On revert the assertion fails (or the
    call raises) fast -- never hangs (anti-hang-test-protocol)."""
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)

    deep = "rules:\n  - pattern: " + ("[" * 20000) + ("]" * 20000) + '\n    severity: "s"\n'
    assert len(deep) < mcp_server._MAX_INLINE_RULES_CHARS, (
        "payload must be sub-cap to test the loader, not the length bound"
    )

    payload = json.loads(mcp_server.tg_ruleset_scan(inline_rules=deep, path="."))

    assert payload.get("error", {}).get("code") == "invalid_input", (
        f"deep-nested YAML must fail closed as invalid_input, not a raw traceback; got: {payload}"
    )
    assert "YAML" in payload["error"]["message"]
    assert "Traceback" not in payload["error"]["message"]


def test_tg_ruleset_scan_inline_rules_rejects_excessive_rule_count(tmp_path, monkeypatch):
    """[SEC] Unbounded scan fan-out DoS -- audit #95 Part-2 re-gate. Each inline rule is a SEPARATE
    ast-grep pass (~40 ms/rule), so a payload UNDER the 64 KiB length cap can still drive a
    multi-minute scan (~1000 rules -> a >40s hang). The rule COUNT cap (_MAX_INLINE_RULES) is the
    binding bound; a payload exceeding it must be refused fast as invalid_input, BEFORE any scan
    (no ast-grep is invoked -- the count check precedes _run_ast_scan_payload)."""
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)

    # _MAX_INLINE_RULES + 1 valid rules, well under the 64 KiB length cap.
    n = mcp_server._MAX_INLINE_RULES + 1
    rules_yaml = "rules:\n" + "".join(
        f"  - id: r{i}\n    pattern: print($A{i})\n" for i in range(n)
    )
    assert len(rules_yaml) < mcp_server._MAX_INLINE_RULES_CHARS

    payload = json.loads(
        mcp_server.tg_ruleset_scan(inline_rules=rules_yaml, path=".", language="python")
    )

    assert payload["error"]["code"] == "invalid_input"
    assert str(n) in payload["error"]["message"]
    assert str(mcp_server._MAX_INLINE_RULES) in payload["error"]["message"]


def test_tg_ruleset_scan_backend_execution_error_fails_closed(tmp_path, monkeypatch):
    """[SEC] Backend Fail-Closed Contract -- audit #95 Part-2 re-gate. A runtime backend fault
    (BackendExecutionError, a RuntimeError -- e.g. ast-grep failing on an over-long pattern,
    WinError 206) was escaping tg_ruleset_scan's `except (BroadScanRefusedError, ValueError)` as a
    RAW TRACEBACK on a valid payload. It must surface as a structured backend_error instead."""
    from tensor_grep.backends.base import BackendExecutionError
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)

    def _boom(*args, **kwargs):
        raise BackendExecutionError("ast-grep failed: [WinError 206] the filename is too long")

    monkeypatch.setattr(mcp_server, "_run_ast_scan_payload", _boom)

    inline_rules = "rules:\n  - id: x\n    pattern: print($A)\n"
    payload = json.loads(
        mcp_server.tg_ruleset_scan(inline_rules=inline_rules, path=".", language="python")
    )

    assert payload["error"]["code"] == "backend_error"
    assert "backend failed" in payload["error"]["message"].lower()
    assert "Traceback" not in payload["error"]["message"]


def test_tg_ruleset_scan_configuration_error_fails_closed(tmp_path, monkeypatch):
    """[SEC] round-4 gate: ast-grep is NOT a declared dependency, so on a DEFAULT
    `pip install tensor-grep` a trivial one-line inline rule reaches
    _select_ast_backend_for_pattern, which raises ConfigurationError (a RuntimeError, NOT a
    ValueError/BackendExecutionError). That escaped tg_ruleset_scan as a RAW TRACEBACK on the
    common default-install path. Must fail closed as a structured 'unavailable'."""
    from tensor_grep.cli import mcp_server
    from tensor_grep.core.pipeline import ConfigurationError

    monkeypatch.chdir(tmp_path)

    def _boom(*a, **k):
        raise ConfigurationError("ast-grep binary not found on PATH; install ast-grep")

    monkeypatch.setattr(mcp_server, "_run_ast_scan_payload", _boom)

    payload = json.loads(
        mcp_server.tg_ruleset_scan(
            inline_rules="rules:\n  - id: x\n    pattern: print($A)\n", path=".", language="python"
        )
    )

    assert payload["error"]["code"] == "unavailable"
    assert "Traceback" not in payload["error"]["message"]


def test_tg_ruleset_scan_baseline_io_error_fails_closed(tmp_path, monkeypatch):
    """[SEC] round-4 gate: an unreadable caller-supplied baseline/suppressions path (e.g. a
    directory) makes _load_ruleset_baseline's read_text raise OSError/IsADirectoryError (NOT a
    ValueError). That escaped as a RAW TRACEBACK. Must fail closed structured, never a traceback."""
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)

    def _boom(*a, **k):
        raise IsADirectoryError("[Errno 21] Is a directory: 'baseline'")

    monkeypatch.setattr(mcp_server, "_run_ast_scan_payload", _boom)

    payload = json.loads(
        mcp_server.tg_ruleset_scan(
            inline_rules="rules:\n  - id: x\n    pattern: print($A)\n", path=".", language="python"
        )
    )

    assert payload["error"]["code"] == "invalid_input"
    assert "Traceback" not in payload["error"]["message"]


def test_tg_ruleset_scan_inline_rules_bad_language_override_fails_closed(tmp_path, monkeypatch):
    """[SEC] round-5 gate: a rule carrying its OWN valid `language:` short-circuits the loader's
    guarded default_language normalization, so an unsupported top-level `language=` override reaches
    normalize_ast_language (mcp_server.py:2008) UNGUARDED -- it was a raw ValueError traceback on a
    valid-but-bogus payload. Must fail closed as structured invalid_input. (The control -- a rule
    that OMITS its own language -- was already caught by the loader; this is the short-circuit gap.)
    """
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)

    # rule sets language=python (so the loader succeeds) + a bogus top-level override reaches 2008.
    payload = json.loads(
        mcp_server.tg_ruleset_scan(
            inline_rules="rules:\n  - id: x\n    language: python\n    pattern: print($A)\n",
            path=".",
            language="zzznotalang",
        )
    )

    assert payload["error"]["code"] == "invalid_input"
    assert "Unsupported AST language" in payload["error"]["message"]
    assert "Traceback" not in payload["error"]["message"]


def test_tg_ruleset_scan_inline_rules_at_length_boundary_still_parses(monkeypatch, tmp_path):
    """Boundary correctness for the length bound: a payload AT the cap must still reach the
    parser (not be off-by-one refused) and behave exactly like any other invalid-but-in-budget
    input -- i.e. still get the ordinary 'no valid inline rules' error, not the length error."""
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    at_cap = "#" + "a" * (mcp_server._MAX_INLINE_RULES_CHARS - 1)
    assert len(at_cap) == mcp_server._MAX_INLINE_RULES_CHARS

    payload = json.loads(mcp_server.tg_ruleset_scan(inline_rules=at_cap, path="."))

    assert payload["error"]["code"] == "invalid_input"
    assert "exceeds" not in payload["error"]["message"].lower()


def test_tg_ruleset_scan_inline_rules_confines_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    outside = tmp_path.parent / f"outside-{tmp_path.name}"
    outside.mkdir(exist_ok=True)
    try:
        payload = json.loads(
            mcp_server.tg_ruleset_scan(
                inline_rules="id: x\nrule:\n  pattern: y\n", path=str(outside)
            )
        )
        assert payload["error"]["code"] == "invalid_input"
        assert "must stay within" in payload["error"]["message"]
    finally:
        outside.rmdir()


def test_tg_ruleset_scan_refuses_direct_temp_root_before_walking(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server
    from tests.unit.test_cli_modes_shared import _ExplodingAstScanner

    temp_root = tmp_path / "Temp"
    temp_root.mkdir()
    (temp_root / "a.py").write_text("API_KEY = 'secret'\n", encoding="utf-8")
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _ExplodingAstScanner)

    payload = json.loads(
        mcp_server.tg_ruleset_scan("secrets-basic", path=str(temp_root), language="python")
    )

    assert payload["routing_reason"] == "builtin-ruleset-scan"
    assert payload["error"]["code"] == "broad_scan_refused"
    assert "broad AST scan refused" in payload["error"]["message"]
    assert "--allow-broad-generated-scan" in payload["error"]["message"]


def test_tg_ruleset_scan_can_emit_evidence_snippets(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server
    from tests.unit.test_cli_modes_shared import _FakeAstPipeline, _FakeAstScanner

    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)
    monkeypatch.chdir(tmp_path)

    Path("a.py").write_text("hashlib.md5($$$ARGS)\n", encoding="utf-8")
    Path("b.py").write_text("ok\n", encoding="utf-8")

    payload = json.loads(
        mcp_server.tg_ruleset_scan(
            "crypto-safe",
            path=".",
            language="python",
            include_evidence_snippets=True,
            max_evidence_snippets_per_file=1,
            max_evidence_snippet_chars=12,
        )
    )

    assert payload["findings"][0]["evidence"][0]["snippets"] == [
        {"text": "hashlib.md5(", "truncated": True}
    ]


def test_tg_ruleset_scan_can_compare_and_write_baseline(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server
    from tests.unit.test_cli_modes_shared import _FakeAstPipeline, _FakeAstScanner

    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)
    monkeypatch.chdir(tmp_path)

    Path("a.py").write_text("hashlib.md5($$$ARGS)\n", encoding="utf-8")
    Path("baseline.json").write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "ruleset-scan-baseline",
                "ruleset": "crypto-safe",
                "language": "python",
                "fingerprints": [
                    hashlib.sha256(
                        json.dumps(
                            {
                                "rule_id": "python-hashlib-md5",
                                "language": "python",
                                "files": ["a.py"],
                            },
                            sort_keys=True,
                        ).encode("utf-8")
                    ).hexdigest()
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    payload = json.loads(
        mcp_server.tg_ruleset_scan(
            "crypto-safe",
            path=".",
            language="python",
            baseline_path="baseline.json",
            write_baseline="written-baseline.json",
        )
    )
    written = json.loads(Path("written-baseline.json").read_text(encoding="utf-8"))

    assert payload["findings"][0]["status"] == "existing"
    assert payload["baseline"]["existing_findings"] == 1
    assert payload["baseline_written"]["count"] == 1
    assert written["fingerprints"] == [payload["findings"][0]["fingerprint"]]


def test_tg_ruleset_scan_can_apply_suppressions(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server
    from tests.unit.test_cli_modes_shared import _FakeAstPipeline, _FakeAstScanner

    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)
    monkeypatch.chdir(tmp_path)

    Path("a.py").write_text("hashlib.md5($$$ARGS)\n", encoding="utf-8")
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "rule_id": "python-hashlib-md5",
                "language": "python",
                "files": ["a.py"],
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    Path("suppressions.json").write_text(
        json.dumps(
            {"version": 1, "kind": "ruleset-scan-suppressions", "fingerprints": [fingerprint]},
            indent=2,
        ),
        encoding="utf-8",
    )

    payload = json.loads(
        mcp_server.tg_ruleset_scan(
            "crypto-safe",
            path=".",
            language="python",
            suppressions_path="suppressions.json",
        )
    )

    assert payload["findings"][0]["status"] == "suppressed"
    assert payload["suppressions"]["suppressed_findings"] == 1


def test_tg_ruleset_scan_can_write_suppressions(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server
    from tests.unit.test_cli_modes_shared import _FakeAstPipeline, _FakeAstScanner

    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)
    monkeypatch.chdir(tmp_path)

    Path("a.py").write_text("hashlib.md5($$$ARGS)\n", encoding="utf-8")

    payload = json.loads(
        mcp_server.tg_ruleset_scan(
            "crypto-safe",
            path=".",
            language="python",
            write_suppressions="written-suppressions.json",
            justification="Approved suppression for fixture coverage.",
        )
    )
    written = json.loads(Path("written-suppressions.json").read_text(encoding="utf-8"))

    assert payload["suppressions_written"]["count"] == 1
    assert written["entries"][0]["fingerprint"] == payload["findings"][0]["fingerprint"]
    assert written["entries"][0]["justification"] == "Approved suppression for fixture coverage."
    assert written["entries"][0]["created_at"].endswith("Z")


def test_tg_ruleset_scan_write_suppressions_requires_justification(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server
    from tests.unit.test_cli_modes_shared import _FakeAstPipeline, _FakeAstScanner

    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)
    monkeypatch.chdir(tmp_path)

    Path("a.py").write_text("hashlib.md5($$$ARGS)\n", encoding="utf-8")

    payload = json.loads(
        mcp_server.tg_ruleset_scan(
            "crypto-safe",
            path=".",
            language="python",
            write_suppressions="written-suppressions.json",
        )
    )

    assert payload["error"]["code"] == "invalid_input"
    assert "justification" in payload["error"]["message"]
