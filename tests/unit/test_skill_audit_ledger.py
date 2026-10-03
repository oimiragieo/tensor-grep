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
    out = mod.build_ledger(mod.repo_root())
    return out


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
    assert re.fullmatch(r"[0-9a-f]{40}", ledger["head_sha"])


def test_manifest_blob_oid_matches_git_ls_files(ledger):
    """Positive control: an independently-run git ls-files -s agrees on a real path."""
    row = ledger["skill_manifest"][0]
    proc = subprocess.run(
        ["git", "ls-files", "-s", "--", row["path"]],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    meta, _, path = proc.stdout.strip().partition("\t")
    assert path == row["path"]
    assert meta.split()[1] == row["blob_oid"]


def test_parse_ls_files_stage_rejects_malformed_rows():
    mod = _load()
    rows = mod.parse_ls_files_stage("100644 abc123 0\ta/b.md\ngarbage\n")
    assert rows == [{"path": "a/b.md", "blob_oid": "abc123"}]
