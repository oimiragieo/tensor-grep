"""Tests for scripts/skill_audit_ledger.py (the tg-skill-audit ledger)."""

from __future__ import annotations

import importlib.util
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "skill_audit_ledger.py"
WORKFLOW = ROOT / ".claude" / "workflows" / "tg-skill-audit.js"


def _git(*argv: str) -> str:
    proc = subprocess.run(
        ["git", *argv],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=True,
    )
    return proc.stdout


def _load():
    spec = importlib.util.spec_from_file_location("skill_audit_ledger", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _schema_required() -> dict[str, list[str]]:
    src = WORKFLOW.read_text(encoding="utf-8")
    block = src[src.index("const LEDGER_SCHEMA") : src.index("const AUDIT_SCHEMA")]
    reqs = [
        [k.strip().strip("'") for k in m.split(",")]
        for m in re.findall(r"required: \[([^\]]*)\]", block)
    ]
    # order in the schema: top level, skill_manifest item, facts item
    assert len(reqs) == 3, reqs
    return {"top": reqs[0], "manifest": reqs[1], "facts": reqs[2]}


@pytest.fixture(scope="module")
def ledger():
    mod = _load()
    # offline: hermetic, no PyPI request and no installed-`tg` dependency
    return mod.build_ledger(ROOT, offline=True)


def test_ledger_keys_match_workflow_schema(ledger):
    req = _schema_required()
    assert set(ledger) == set(req["top"])
    assert ledger["skill_manifest"], "no tracked skill files reported"
    for row in ledger["skill_manifest"]:
        assert set(row) == set(req["manifest"])
    assert ledger["facts"]
    for f in ledger["facts"]:
        assert set(f) == set(req["facts"])
        assert all(isinstance(v, str) for v in f.values())
    for key in ("repo_root", "head_sha", "git_status", "raw_output"):
        assert isinstance(ledger[key], str)


def test_head_sha_and_status_match_git(ledger):
    assert ledger["head_sha"] == _git("rev-parse", "HEAD").strip()
    assert ledger["git_status"] == _git("status", "--porcelain").rstrip("\n")


def test_manifest_equals_independent_git_ls_files(ledger):
    expected = set()
    for line in _git("ls-files", "-s", "--", ".claude/skills/").splitlines():
        meta, _, path = line.partition("\t")
        expected.add((path, meta.split()[1]))
    assert expected, "positive control: the repo must track skill files"
    got = [(r["path"], r["blob_oid"]) for r in ledger["skill_manifest"]]
    assert len(got) == len(expected)
    assert set(got) == expected


def test_offline_skips_network_and_tg_facts(ledger):
    values = {f["name"]: f["value"] for f in ledger["facts"]}
    assert values["pypi_version"] == "SKIPPED: --offline"
    assert any(v == "SKIPPED: --offline" for k, v in values.items() if k.startswith("tg_version"))


def test_parse_ls_files_stage_rejects_malformed_rows():
    mod = _load()
    rows = mod.parse_ls_files_stage("100644 abc123 0\ta/b.md\ngarbage\n")
    assert rows == [{"path": "a/b.md", "blob_oid": "abc123"}]
