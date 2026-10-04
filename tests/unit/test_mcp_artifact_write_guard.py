"""MCP artifact-write guard: refuse non-artifact overwrites, carry the authorization into the
write (TOCTOU), create missing parents, and keep multi-output requests honest.

Split out of test_mcp_server_path_confinement.py (file-size ratchet)."""

import json
import os
from pathlib import Path

import pytest

from tests.unit.test_mcp_server_shared import _write_audit_manifest


@pytest.mark.parametrize(
    "target", ["a.py", ".git/config", ".GIT/config", "notes.txt", "other.json"]
)
def test_ruleset_scan_write_baseline_refuses_non_artifact_target(tmp_path, monkeypatch, target):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
    victim = tmp_path / target
    victim.parent.mkdir(parents=True, exist_ok=True)
    victim.write_bytes(b"{}" if target == "other.json" else b"ORIGINAL\n")
    before = victim.read_bytes()
    out = json.loads(mcp_server.tg_ruleset_scan("secrets-basic", path=".", write_baseline=target))
    assert out["error"]["code"] == "invalid_input"
    assert "must stay within" not in out["error"]["message"]
    assert victim.read_bytes() == before


def test_ruleset_scan_new_json_baseline_is_still_allowed(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
    out = json.loads(
        mcp_server.tg_ruleset_scan("secrets-basic", path=".", write_baseline="base.json")
    )
    assert "error" not in out
    written = json.loads((tmp_path / "base.json").read_text(encoding="utf-8"))
    assert written["kind"] == "ruleset-scan-baseline"


def test_ruleset_scan_write_suppressions_and_bundle_refuse_source_overwrite(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    before = (tmp_path / "a.py").read_bytes()
    sup = json.loads(
        mcp_server.tg_ruleset_scan(
            "secrets-basic", path=".", write_suppressions="a.py", justification="x"
        )
    )
    assert sup["error"]["code"] == "invalid_input"
    bundle = json.loads(
        mcp_server.tg_review_bundle_create(manifest_path=str(manifest), output_path="a.py")
    )
    assert bundle["error"]["code"] == "invalid_input"
    # Non-vacuous: the refusal must come from the artifact gate, not unrelated manifest validation.
    assert (
        "must be a new .json file or an existing tensor-grep artifact"
        in (bundle["error"]["message"])
    )
    assert (tmp_path / "a.py").read_bytes() == before


@pytest.mark.parametrize("body", ['{"kind": []}', '{"routing_reason": {"x": 1}}', "[]", '"s"'])
def test_existing_json_with_non_string_discriminator_is_refused_not_crash(
    tmp_path, monkeypatch, body
):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
    victim = tmp_path / "weird.json"
    victim.write_text(body, encoding="utf-8")
    out = json.loads(
        mcp_server.tg_ruleset_scan("secrets-basic", path=".", write_baseline="weird.json")
    )
    assert out["error"]["code"] == "invalid_input"
    assert victim.read_text(encoding="utf-8") == body


_NINE_MIB = 9 * 1024 * 1024


def _scan_with_baseline_target(tmp_path, monkeypatch, name):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
    return json.loads(mcp_server.tg_ruleset_scan("secrets-basic", path=".", write_baseline=name))


def test_large_baseline_with_kind_as_last_key_is_accepted_for_rerun(tmp_path, monkeypatch):
    victim = tmp_path / "big.json"
    padding = '"' + ("p" * 64) + '"'
    count = _NINE_MIB // (len(padding) + 2)
    victim.write_text(
        '{"findings": [' + ", ".join([padding] * count) + '], "kind": "ruleset-scan-baseline"}',
        encoding="utf-8",
    )
    assert victim.stat().st_size >= _NINE_MIB
    out = _scan_with_baseline_target(tmp_path, monkeypatch, "big.json")
    assert "error" not in out
    assert victim.stat().st_size < _NINE_MIB  # overwritten by the fresh (small) baseline
    assert json.loads(victim.read_text(encoding="utf-8"))["kind"] == "ruleset-scan-baseline"


def test_large_json_with_duplicate_kind_keys_uses_the_effective_last_value(tmp_path, monkeypatch):
    victim = tmp_path / "dup.json"
    padding = '"' + ("p" * 64) + '"'
    count = _NINE_MIB // (len(padding) + 2)
    victim.write_text(
        '{"kind": "ruleset-scan-baseline", "findings": ['
        + ", ".join([padding] * count)
        + '], "kind": "something-else"}',
        encoding="utf-8",
    )
    before = victim.read_bytes()
    out = _scan_with_baseline_target(tmp_path, monkeypatch, "dup.json")
    assert out["error"]["code"] == "invalid_input"
    assert victim.read_bytes() == before


def test_large_malformed_json_target_is_refused_and_unchanged(tmp_path, monkeypatch):
    victim = tmp_path / "bad.json"
    victim.write_text(
        '{"kind": "ruleset-scan-baseline", "x": "' + ("p" * _NINE_MIB), encoding="utf-8"
    )
    before = victim.read_bytes()
    out = _scan_with_baseline_target(tmp_path, monkeypatch, "bad.json")
    assert out["error"]["code"] == "invalid_input"
    assert victim.read_bytes() == before


def test_existing_artifact_over_the_probe_cap_is_refused(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_artifact_guard

    monkeypatch.setattr(mcp_artifact_guard, "_MCP_ARTIFACT_PROBE_MAX_BYTES", 1024)
    victim = tmp_path / "over.json"
    victim.write_text(
        '{"kind": "ruleset-scan-baseline", "pad": "' + ("p" * 2048) + '"}', encoding="utf-8"
    )
    assert victim.stat().st_size > 2048
    before = victim.read_bytes()
    out = _scan_with_baseline_target(tmp_path, monkeypatch, "over.json")
    assert out["error"]["code"] == "invalid_input"
    assert victim.read_bytes() == before


# --- Codex round 1, finding 1: artifact authorization must survive until the write (TOCTOU) ---


def _swap_before_scan_write(monkeypatch, mutate):
    """Seam between the artifact guard and the writer: run ``mutate()`` AFTER confinement
    approved the target and BEFORE ``_run_ast_scan_payload`` writes it."""
    from tensor_grep.cli import mcp_server

    real = mcp_server._run_ast_scan_payload

    def gated(*args, **kwargs):
        mutate()
        return real(*args, **kwargs)

    monkeypatch.setattr(mcp_server, "_run_ast_scan_payload", gated)


@pytest.mark.parametrize("param", ["write_baseline", "write_suppressions"])
def test_absent_target_that_becomes_a_non_artifact_before_the_write_is_not_clobbered(
    tmp_path, monkeypatch, param
):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
    victim = tmp_path / "late.json"
    assert not victim.exists()
    _swap_before_scan_write(monkeypatch, lambda: victim.write_bytes(b'{"mine": true}\n'))
    kwargs = {param: "late.json"}
    if param == "write_suppressions":
        kwargs["justification"] = "x"
    out = json.loads(mcp_server.tg_ruleset_scan("secrets-basic", path=".", **kwargs))
    assert out["error"]["code"] == "invalid_input"
    assert victim.read_bytes() == b'{"mine": true}\n'
    assert list(tmp_path.glob(".late.json.*.tmp")) == []


@pytest.mark.parametrize("param", ["write_baseline", "write_suppressions"])
def test_approved_artifact_replaced_by_unrelated_json_before_the_write_is_not_clobbered(
    tmp_path, monkeypatch, param
):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
    victim = tmp_path / "art.json"
    kwargs = {param: "art.json"}
    if param == "write_suppressions":
        kwargs["justification"] = "x"
    first = json.loads(mcp_server.tg_ruleset_scan("secrets-basic", path=".", **kwargs))
    assert "error" not in first  # positive control: a normal new write
    assert victim.exists()
    unrelated = b'{"unrelated": "json that is much longer than the artifact kind marker"}\n'
    _swap_before_scan_write(monkeypatch, lambda: victim.write_bytes(unrelated))
    out = json.loads(mcp_server.tg_ruleset_scan("secrets-basic", path=".", **kwargs))
    assert out["error"]["code"] == "invalid_input"
    assert victim.read_bytes() == unrelated


def test_normal_rerun_overwrite_of_an_approved_artifact_still_works(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
    for _ in range(2):
        out = json.loads(
            mcp_server.tg_ruleset_scan("secrets-basic", path=".", write_baseline="again.json")
        )
        assert "error" not in out
    assert json.loads((tmp_path / "again.json").read_text(encoding="utf-8"))["kind"] == (
        "ruleset-scan-baseline"
    )


def _swap_before_bundle_write(monkeypatch, mutate):
    from tensor_grep.cli import audit_manifest

    real = audit_manifest.create_review_bundle_json

    def gated(*args, **kwargs):
        mutate()
        return real(*args, **kwargs)

    monkeypatch.setattr(audit_manifest, "create_review_bundle_json", gated)


def test_review_bundle_output_is_not_clobbered_when_target_changes_after_the_check(
    tmp_path, monkeypatch
):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    manifest = tmp_path / "manifest.json"
    _write_audit_manifest(manifest)
    # positive controls: a normal new write, then a normal rerun overwrite
    for _ in range(2):
        ok = json.loads(
            mcp_server.tg_review_bundle_create(manifest_path=str(manifest), output_path="b.json")
        )
        assert "error" not in ok, ok
    bundle = tmp_path / "b.json"
    unrelated = b'{"unrelated": "json that is much longer than the bundle marker"}\n'
    _swap_before_bundle_write(monkeypatch, lambda: bundle.write_bytes(unrelated))
    out = json.loads(
        mcp_server.tg_review_bundle_create(manifest_path=str(manifest), output_path="b.json")
    )
    assert out["error"]["code"] == "invalid_input"
    assert bundle.read_bytes() == unrelated

    late = tmp_path / "late_bundle.json"
    _swap_before_bundle_write(monkeypatch, lambda: late.write_bytes(b'{"mine": 1}\n'))
    out2 = json.loads(
        mcp_server.tg_review_bundle_create(
            manifest_path=str(manifest), output_path="late_bundle.json"
        )
    )
    assert out2["error"]["code"] == "invalid_input"
    assert late.read_bytes() == b'{"mine": 1}\n'


def _link_dir(link: Path, target: Path) -> None:
    """Symlink, or a directory junction on Windows when symlinks are not permitted."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt":
        import subprocess

        done = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, check=False
        )
        if done.returncode == 0:
            return
    pytest.skip("neither symlinks nor junctions are permitted here")


@pytest.mark.parametrize("pre_existing", [True, False])
def test_parent_swapped_for_a_link_before_the_write_is_refused(tmp_path, monkeypatch, pre_existing):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    other = tmp_path.parent / (tmp_path.name + "_other")
    other.mkdir()
    foreign = other / "victim.json"
    foreign.write_bytes(b'{"not": "an artifact"}\n')
    if pre_existing:
        first = json.loads(
            mcp_server.tg_ruleset_scan("secrets-basic", path=".", write_baseline="sub/victim.json")
        )
        assert "error" not in first  # positive control: the normal write works

    def swap_parent():
        sub.rename(tmp_path / "sub_real")
        _link_dir(sub, other)

    _swap_before_scan_write(monkeypatch, swap_parent)
    out = json.loads(
        mcp_server.tg_ruleset_scan("secrets-basic", path=".", write_baseline="sub/victim.json")
    )
    assert out["error"]["code"] == "invalid_input"
    assert foreign.read_bytes() == b'{"not": "an artifact"}\n'
    assert [p.name for p in other.iterdir()] == ["victim.json"]  # no temp left in the other dir


def test_write_to_an_unauthorized_path_inside_an_active_scope_fails_closed(tmp_path):
    from tensor_grep.cli import _index_lock

    allowed = tmp_path / "ok.json"
    auth = _index_lock.WriteAuthorization(str(allowed), None, _index_lock.dir_identity(tmp_path))
    elsewhere = tmp_path / "elsewhere.json"
    with _index_lock.write_authorizations([auth]):
        with pytest.raises(_index_lock.WriteAuthorizationError):
            _index_lock.atomic_write_bytes(elsewhere, b"x")
        _index_lock.atomic_write_bytes(allowed, b"{}")  # the authorized path still works
    assert not elsewhere.exists()
    assert allowed.read_bytes() == b"{}"
    _index_lock.atomic_write_bytes(elsewhere, b"x")  # outside any scope: unchanged behaviour
    assert elsewhere.read_bytes() == b"x"


def test_replace_retry_rechecks_identity_before_each_attempt(tmp_path, monkeypatch):
    from tensor_grep.cli import _index_lock

    target = tmp_path / "t.json"
    target.write_bytes(b'{"kind": "k"}')
    auth = _index_lock.WriteAuthorization(
        str(target),
        _index_lock.file_identity(target),
        _index_lock.dir_identity(tmp_path),
    )
    calls = {"replace": 0}
    real_replace = os.replace

    def flaky_replace(src, dst):
        calls["replace"] += 1
        if calls["replace"] == 1:
            target.write_bytes(b'{"swapped": "while the retry slept, longer content"}')
            raise PermissionError("transient")
        real_replace(src, dst)

    monkeypatch.setattr(_index_lock.os, "replace", flaky_replace)
    monkeypatch.setattr(_index_lock.time, "sleep", lambda _s: None)
    with _index_lock.write_authorizations([auth]):
        with pytest.raises(_index_lock.WriteAuthorizationError):
            _index_lock.atomic_write_bytes(target, b'{"kind": "k"}')
    assert calls["replace"] == 1  # the second attempt never happened
    assert b"swapped" in target.read_bytes()


# --- Codex round 3: missing-parent artifact paths, and no leftover temp hard link ---


def test_new_nested_baseline_path_with_missing_parents_is_published(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
    assert not (tmp_path / "a").exists()
    out = json.loads(
        mcp_server.tg_ruleset_scan("secrets-basic", path=".", write_baseline="a/b/base.json")
    )
    assert "error" not in out, out
    written = json.loads((tmp_path / "a" / "b" / "base.json").read_text(encoding="utf-8"))
    assert written["kind"] == "ruleset-scan-baseline"
    assert [p.name for p in (tmp_path / "a" / "b").iterdir()] == ["base.json"]


def test_new_nested_review_bundle_path_with_missing_parents_is_published(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    manifest = tmp_path / "manifest.json"
    _write_audit_manifest(manifest)
    out = json.loads(
        mcp_server.tg_review_bundle_create(
            manifest_path=str(manifest), output_path="reports/deep/bundle.json"
        )
    )
    assert "error" not in out, out
    bundle = tmp_path / "reports" / "deep" / "bundle.json"
    assert json.loads(bundle.read_text(encoding="utf-8"))["routing_reason"] == (
        "review-bundle-create"
    )
    assert [p.name for p in bundle.parent.iterdir()] == ["bundle.json"]


def test_missing_parent_created_by_someone_else_before_the_write_is_refused(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
    other = tmp_path.parent / (tmp_path.name + "_elsewhere")
    other.mkdir()

    def someone_else_creates_the_parent_as_a_link():
        _link_dir(tmp_path / "reports", other)

    _swap_before_scan_write(monkeypatch, someone_else_creates_the_parent_as_a_link)
    out = json.loads(
        mcp_server.tg_ruleset_scan("secrets-basic", path=".", write_baseline="reports/base.json")
    )
    assert out["error"]["code"] == "invalid_input"
    assert list(other.iterdir()) == []


@pytest.mark.parametrize("pre_existing", [False, True])
def test_publish_leaves_only_the_authorized_artifact_in_the_directory(
    tmp_path, monkeypatch, pre_existing
):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    runs = 2 if pre_existing else 1  # run 1 = no-clobber publish, run 2 = replace publish
    for _ in range(runs):
        out = json.loads(
            mcp_server.tg_ruleset_scan("secrets-basic", path=".", write_baseline="out/base.json")
        )
        assert "error" not in out, out
        assert sorted(p.name for p in out_dir.iterdir()) == ["base.json"]
        assert os.stat(out_dir / "base.json").st_nlink == 1


def test_unauthorized_scope_writer_leaves_no_temp_names_on_success(tmp_path):
    from tensor_grep.cli import _index_lock

    target = tmp_path / "plain.json"
    _index_lock.atomic_write_bytes_anchored(target, b"{}", replace=False)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["plain.json"]
    assert os.stat(target).st_nlink == 1
    _index_lock.atomic_write_bytes_anchored(target, b"{}", replace=True)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["plain.json"]


# --- Codex round 4: paired outputs share missing directories; multi-output partial writes ---


def _paired_scan(mcp_server, baseline, suppressions):
    return json.loads(
        mcp_server.tg_ruleset_scan(
            "secrets-basic",
            path=".",
            write_baseline=baseline,
            write_suppressions=suppressions,
            justification="reviewed",
        )
    )


def test_paired_outputs_sharing_an_identical_missing_parent_both_publish(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
    out = _paired_scan(mcp_server, "reports/base.json", "reports/suppressions.json")
    assert "error" not in out, out
    assert sorted(p.name for p in (tmp_path / "reports").iterdir()) == [
        "base.json",
        "suppressions.json",
    ]


def test_paired_outputs_under_a_shared_missing_ancestor_both_publish(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
    out = _paired_scan(mcp_server, "reports/base/base.json", "reports/sup/suppressions.json")
    assert "error" not in out, out
    assert (tmp_path / "reports" / "base" / "base.json").is_file()
    assert (tmp_path / "reports" / "sup" / "suppressions.json").is_file()
    assert sorted(p.name for p in (tmp_path / "reports").iterdir()) == ["base", "sup"]


def _after_first_publish(monkeypatch, action):
    """Run ``action()`` once, right AFTER the first no-clobber publish of the scope."""
    from tensor_grep.cli import _index_lock

    real = _index_lock._publish_bytes_no_clobber
    fired = []

    def wrapped(src, dst):
        real(src, dst)
        if not fired:
            fired.append(True)
            action()

    monkeypatch.setattr(_index_lock, "_publish_bytes_no_clobber", wrapped)


def test_shared_directory_swapped_between_the_two_writes_is_refused(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
    other = tmp_path.parent / (tmp_path.name + "_swap_target")
    other.mkdir()

    def swap_shared_dir():
        (tmp_path / "reports").rename(tmp_path / "reports_real")
        _link_dir(tmp_path / "reports", other)

    _after_first_publish(monkeypatch, swap_shared_dir)
    out = _paired_scan(mcp_server, "reports/base.json", "reports/suppressions.json")
    assert out["error"]["code"] == "invalid_input"
    assert list(other.iterdir()) == []
    assert (tmp_path / "reports_real" / "base.json").is_file()


def _mixed_paired_request(tmp_path, mcp_server):
    return _paired_scan(mcp_server, "new/base.json", "existing/suppressions.json")


def test_multi_output_request_is_refused_before_any_output_is_published(tmp_path, monkeypatch):
    """Chosen behaviour (a): every output is re-validated BEFORE the first one publishes."""
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "existing").mkdir()
    victim = tmp_path / "existing" / "suppressions.json"
    _swap_before_scan_write(monkeypatch, lambda: victim.write_bytes(b'{"mine": 1}\n'))
    out = _mixed_paired_request(tmp_path, mcp_server)
    assert out["error"]["code"] == "invalid_input"
    assert victim.read_bytes() == b'{"mine": 1}\n'
    assert not (tmp_path / "new").exists()  # nothing was published or created


def test_late_race_after_the_first_publish_names_the_outputs_already_written(tmp_path, monkeypatch):
    """Fallback (b): a refusal that can only be seen mid-way reports what was written."""
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "existing").mkdir()
    victim = tmp_path / "existing" / "suppressions.json"
    _after_first_publish(monkeypatch, lambda: victim.write_bytes(b'{"mine": 1}\n'))
    out = _mixed_paired_request(tmp_path, mcp_server)
    assert out["error"]["code"] == "invalid_input"
    assert "already written" in out["error"]["message"]
    assert "write_baseline" in out["error"]["message"]
    assert victim.read_bytes() == b'{"mine": 1}\n'
    assert (tmp_path / "new" / "base.json").is_file()


# --- Codex round 5: preflight must walk EVERY component between the ancestor and the parent ---


@pytest.mark.parametrize(
    ("second", "external"),
    [
        ("reports/deep/suppressions.json", "reports"),
        ("a/b/c/suppressions.json", "a/b"),
    ],
)
def test_externally_created_intermediate_directory_is_refused_before_any_publish(
    tmp_path, monkeypatch, second, external
):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
    _swap_before_scan_write(
        monkeypatch, lambda: (tmp_path / external).mkdir(parents=True, exist_ok=False)
    )
    out = _paired_scan(mcp_server, "new/base.json", second)
    assert out["error"]["code"] == "invalid_input"
    assert not (tmp_path / "new").exists()  # nothing published, no directory created
    assert list((tmp_path / external).iterdir()) == []


def test_deep_paired_outputs_with_no_external_interference_still_publish(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
    out = _paired_scan(mcp_server, "new/base.json", "reports/deep/suppressions.json")
    assert "error" not in out, out
    assert (tmp_path / "new" / "base.json").is_file()
    assert (tmp_path / "reports" / "deep" / "suppressions.json").is_file()


# --- Codex round 6: the whole output SET is validated before anything is created or published ---


def _only_src(tmp_path):
    return sorted(p.name for p in tmp_path.iterdir())


@pytest.mark.parametrize(
    ("baseline", "suppressions"),
    [
        ("both.json", "both.json"),
        ("reports/base.json", "reports/base.json/suppressions.json"),
        ("reports/sub.json/base.json", "reports/sub.json"),
    ],
)
def test_conflicting_output_paths_are_refused_before_anything_is_created(
    tmp_path, monkeypatch, baseline, suppressions
):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
    before = _only_src(tmp_path)
    out = _paired_scan(mcp_server, baseline, suppressions)
    assert out["error"]["code"] == "invalid_input"
    assert "write_baseline" in out["error"]["message"]
    assert "write_suppressions" in out["error"]["message"]
    assert "already written" not in out["error"]["message"]
    assert _only_src(tmp_path) == before  # no file, no directory


def test_case_only_duplicate_output_paths(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
    out = _paired_scan(mcp_server, "Both.json", "both.json")
    if os.name == "nt":  # case-insensitive filesystem: the same file
        assert out["error"]["code"] == "invalid_input"
        assert "write_baseline" in out["error"]["message"]
        assert "write_suppressions" in out["error"]["message"]
        assert not (tmp_path / "Both.json").exists()
    else:  # case-sensitive: genuinely distinct siblings, the CI matrix decides
        assert "error" not in out, out
        assert (tmp_path / "Both.json").is_file() and (tmp_path / "both.json").is_file()


def test_two_distinct_sibling_outputs_both_publish(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
    out = _paired_scan(mcp_server, "base.json", "suppressions.json")
    assert "error" not in out, out
    assert (tmp_path / "base.json").is_file()
    assert (tmp_path / "suppressions.json").is_file()


def test_existing_aliases_of_one_file_are_refused_by_resolved_identity(tmp_path):
    from tensor_grep.cli import _index_lock

    real = tmp_path / "real.json"
    real.write_text("{}", encoding="utf-8")
    try:
        os.link(real, tmp_path / "alias.json")
    except OSError:
        pytest.skip("hard links are not permitted here")
    root = _index_lock.dir_identity(tmp_path)
    auths = [
        _index_lock.WriteAuthorization(
            str(tmp_path / name), _index_lock.file_identity(tmp_path / name), root, label
        )
        for name, label in (("real.json", "write_baseline"), ("alias.json", "write_suppressions"))
    ]
    scope = _index_lock.write_authorizations(auths)
    with pytest.raises(_index_lock.WriteAuthorizationError) as excinfo:
        with scope:
            pass
    assert "write_baseline" in str(excinfo.value)
    assert "write_suppressions" in str(excinfo.value)


# --- Codex round 13: the .git rule is root-relative; the guard modules import cold ---


@pytest.mark.parametrize("scan_root", [".git", ".git/subdir", "sub/.git", "sub/.GIT/deeper"])
def test_scan_root_inside_dot_git_is_refused_even_for_a_plain_artifact_name(
    tmp_path, monkeypatch, scan_root
):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    (tmp_path / scan_root).mkdir(parents=True)
    (tmp_path / scan_root / "src.py").write_text("x = 1\n", encoding="utf-8")
    before = sorted(p.name for p in (tmp_path / scan_root).iterdir())
    out = json.loads(
        mcp_server.tg_ruleset_scan("secrets-basic", path=scan_root, write_baseline="new.json")
    )
    assert out["error"]["code"] == "invalid_input"
    assert sorted(p.name for p in (tmp_path / scan_root).iterdir()) == before  # nothing written


def test_dot_git_component_below_an_ordinary_anchor_is_still_refused(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
    out = json.loads(
        mcp_server.tg_ruleset_scan("secrets-basic", path=".", write_baseline="sub/.git/new.json")
    )
    assert out["error"]["code"] == "invalid_input"
    assert not (tmp_path / "sub").exists()


def test_legitimate_root_and_nested_anchor_are_still_allowed(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "src.py").write_text("x = 1\n", encoding="utf-8")
    for anchor in (".", "pkg"):
        out = json.loads(
            mcp_server.tg_ruleset_scan("secrets-basic", path=anchor, write_baseline="ok.json")
        )
        assert "error" not in out, out
        assert (tmp_path / anchor / "ok.json").is_file()


_COLD_IMPORT_MODULES = [
    "tensor_grep.cli.mcp_artifact_guard",
    "tensor_grep.cli.mcp_path_errors",
    # mcp_audit_tools is a split-out tail of mcp_server BY DESIGN (it binds `_self` to the already
    # loading server module, see its docstring), so its supported cold entry is via the server.
    "tensor_grep.cli.mcp_server, tensor_grep.cli.mcp_audit_tools",
    "tensor_grep.cli.mcp_server",
    "tensor_grep.cli.mcp_search_bounds",
    "tensor_grep.cli._index_lock",
]


@pytest.mark.parametrize("module", _COLD_IMPORT_MODULES)
def test_each_new_module_imports_cold_in_a_fresh_interpreter(module):
    import subprocess
    import sys

    import tensor_grep

    src_root = str(Path(tensor_grep.__file__).resolve().parents[1])
    env = {**os.environ, "PYTHONPATH": src_root}
    done = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-800:]


def test_guard_symbol_imports_cold_in_a_fresh_interpreter():
    import subprocess
    import sys

    import tensor_grep

    src_root = str(Path(tensor_grep.__file__).resolve().parents[1])
    code = "from tensor_grep.cli.mcp_artifact_guard import _authorize_artifact_write_path"
    done = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": src_root},
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-800:]
