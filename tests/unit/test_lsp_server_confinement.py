"""Workspace-root confinement tests for LSP rename edits (audit MED) and file operations
(audit M3).

An external LSP provider's rename response was applied verbatim (``WorkspaceEdit(**result)``)
with no check that the edited document URIs stayed inside the resolved workspace root, so a
malicious/buggy provider could drive an edit to a file outside the workspace. Both the
external and native rename branches now confine every edit target to the workspace root.

Audit M3 extends that confinement to the LSP ``documentChanges`` *file-operation* members
(``CreateFile`` / ``RenameFile`` / ``DeleteFile``), which carry no ``textDocument`` key and so
were previously invisible to ``_workspace_edit_target_uris``: a file-op-only WorkspaceEdit
collected zero URIs, so the enforcement guard passed VACUOUSLY and out-of-root file-ops were
not confined — an empty guarantee that read as a check. Collection now enumerates all five
target fields and the enforcement is fail-closed: refuse the whole edit if ANY target resolves
outside the workspace root OR any ``documentChanges`` member is opaque (unrecognized shape).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("lsprotocol.types")
pytest.importorskip("pygls.lsp.server")

from lsprotocol.types import (
    DidOpenTextDocumentParams,
    Position,
    TextDocumentItem,
)

import tensor_grep.cli.lsp_server as lsp_module
from tensor_grep.cli.lsp_server import (
    TensorGrepLSPServer,
    _path_to_uri,
    _path_within_root,
    _uri_to_path,
    _uri_within_root,
    _valid_external_document_uri,
    _workspace_edit_for_symbol,
    _workspace_edit_has_opaque_member,
    _workspace_edit_refused,
    _workspace_edit_target_uris,
)


def _open_document(server: TensorGrepLSPServer, path: Path) -> str:
    uri = path.resolve().as_uri()
    lsp_module.did_open(
        server,
        DidOpenTextDocumentParams(
            text_document=TextDocumentItem(
                uri=uri, language_id="python", version=1, text=path.read_text(encoding="utf-8")
            )
        ),
    )
    return uri


def test_path_and_uri_within_root_confine_and_reject(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    inside = root / "pkg" / "mod.py"
    inside.write_text("x", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("x", encoding="utf-8")

    assert _path_within_root(inside, root) is True
    assert _path_within_root(outside, root) is False
    assert _uri_within_root(_path_to_uri(inside), root) is True
    assert _uri_within_root(_path_to_uri(outside), root) is False


def test_workspace_edit_target_uris_extracts_both_shapes() -> None:
    result = {
        "changes": {"file:///a.py": [], "file:///b.py": []},
        "documentChanges": [
            {"textDocument": {"uri": "file:///c.py"}, "edits": []},
        ],
    }
    assert set(_workspace_edit_target_uris(result)) == {
        "file:///a.py",
        "file:///b.py",
        "file:///c.py",
    }


def _inside_and_outside(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    inside = root / "pkg" / "mod.py"
    inside.write_text("def f():\n    return 1\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("def g():\n    return 2\n", encoding="utf-8")
    return root, inside, outside


# --- Audit M3: file-op (CreateFile/RenameFile/DeleteFile) confinement -------------------
#
# Relay residual (relay-only TOCTOU): tg resolves each target synchronously at relay time
# while the IDE applies the edit later, so a filesystem swap between check and apply is an
# inherent relay-only TOCTOU (tg never opens the file). ``_path_within_root`` canonicalizes
# junctions/case/8.3 aliases at CHECK time, so the alias-escape class is covered there; this
# confinement is NOT an opened-identity guarantee.


@pytest.mark.parametrize(
    ("member", "expected_uris"),
    [
        pytest.param(
            {"kind": "create", "uri": "file:///tmp/evil.py"},
            {"file:///tmp/evil.py"},
            id="create",
        ),
        pytest.param(
            {"kind": "delete", "uri": "file:///tmp/victim.py"},
            {"file:///tmp/victim.py"},
            id="delete",
        ),
        pytest.param(
            {"kind": "rename", "oldUri": "file:///tmp/out.py", "newUri": "file:///repo/in.py"},
            {"file:///tmp/out.py", "file:///repo/in.py"},
            id="rename_both_ends",
        ),
    ],
)
def test_workspace_edit_target_uris_collects_file_op_uris(
    member: dict[str, str], expected_uris: set[str]
) -> None:
    """The file-op members were NOT collected pre-fix (a file-op-only edit returned []), so the
    enforcement guard passed vacuously and out-of-root file-ops were not confined. Now each of
    the five target fields is enumerated."""
    result = {"documentChanges": [member]}
    assert set(_workspace_edit_target_uris(result)) >= expected_uris


def test_fileop_create_of_out_of_root_is_refused(tmp_path: Path) -> None:
    root, _, outside = _inside_and_outside(tmp_path)
    result = {"documentChanges": [{"kind": "create", "uri": _path_to_uri(outside)}]}
    assert _workspace_edit_has_opaque_member(result) is False
    assert _workspace_edit_refused(result, root) is True


def test_fileop_delete_of_out_of_root_is_refused(tmp_path: Path) -> None:
    root, _, outside = _inside_and_outside(tmp_path)
    result = {"documentChanges": [{"kind": "delete", "uri": _path_to_uri(outside)}]}
    assert _workspace_edit_refused(result, root) is True


def test_fileop_rename_in_old_uri_out_of_root_is_refused(tmp_path: Path) -> None:
    # oldUri outside, newUri in-repo -> would PLANT content into the workspace.
    root, inside, outside = _inside_and_outside(tmp_path)
    result = {
        "documentChanges": [
            {"kind": "rename", "oldUri": _path_to_uri(outside), "newUri": _path_to_uri(inside)}
        ]
    }
    assert _workspace_edit_refused(result, root) is True


def test_fileop_rename_out_new_uri_out_of_root_is_refused(tmp_path: Path) -> None:
    # oldUri in-repo, newUri outside -> would EXFILTRATE an in-repo file.
    root, inside, outside = _inside_and_outside(tmp_path)
    result = {
        "documentChanges": [
            {"kind": "rename", "oldUri": _path_to_uri(inside), "newUri": _path_to_uri(outside)}
        ]
    }
    assert _workspace_edit_refused(result, root) is True


def test_fileop_missing_required_uri_is_refused_as_opaque(tmp_path: Path) -> None:
    # A recognized KIND with a missing required URI cannot be proven within-root -> refuse.
    root, inside, _ = _inside_and_outside(tmp_path)
    result = {"documentChanges": [{"kind": "rename", "newUri": _path_to_uri(inside)}]}
    assert _workspace_edit_has_opaque_member(result) is True
    assert _workspace_edit_refused(result, root) is True


def test_fileop_opaque_member_is_refused_not_vacuous(tmp_path: Path) -> None:
    # An unknown shape cannot be proven within-root -> refuse, never a vacuous pass.
    root, _, _ = _inside_and_outside(tmp_path)
    result = {
        "documentChanges": [{"kind": "SneakyThing", "path": str(tmp_path / "etc" / "passwd")}]
    }
    assert _workspace_edit_has_opaque_member(result) is True
    assert _workspace_edit_refused(result, root) is True


def test_fileop_mixed_with_out_of_root_member_is_refused(tmp_path: Path) -> None:
    # The dangerous mixed shape: an in-root changes-map target PLUS an out-of-root file-op.
    # The old guard confined only the in-root target and passed the whole edit vacuously.
    root, inside, outside = _inside_and_outside(tmp_path)
    result = {
        "changes": {_path_to_uri(inside): [{"newText": "x"}]},
        "documentChanges": [{"kind": "delete", "uri": _path_to_uri(outside)}],
    }
    assert _workspace_edit_refused(result, root) is True


def test_fileop_all_within_root_edit_is_confined(tmp_path: Path) -> None:
    # Positive control: an in-root text edit plus an in-root file-op is NOT refused.
    root, inside, _ = _inside_and_outside(tmp_path)
    result = {
        "changes": {_path_to_uri(inside): []},
        "documentChanges": [{"kind": "delete", "uri": _path_to_uri(inside)}],
    }
    assert _workspace_edit_has_opaque_member(result) is False
    assert _workspace_edit_refused(result, root) is False


def test_changes_map_only_edit_within_root_is_not_refused(tmp_path: Path) -> None:
    # Positive control: a changes-map edit confined entirely within root is not refused.
    root, inside, _ = _inside_and_outside(tmp_path)
    result = {"changes": {_path_to_uri(inside): [{"newText": "x"}]}}
    assert _workspace_edit_refused(result, root) is False


def test_changes_map_edit_is_forwarded_end_to_end(tmp_path: Path, monkeypatch) -> None:
    """The ``changes``-map form reaches the client (the positive path stays intact under M3).

    The provider supplies a changes-map edit confined to the workspace root; the confinement
    decision must allow it through and the WorkspaceEdit must carry the provider's ``changes``
    map (as opposed to the native branch, which emits ``document_changes`` instead).
    """
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8"
    )
    module_path = tmp_path / "module.py"
    module_path.write_text("def create_invoice() -> None:\n    return None\n", encoding="utf-8")
    inroot_uri = module_path.resolve().as_uri()

    class _FakeClient:
        def ensure_document(self, **kwargs: object) -> None:
            return None

        def request(self, method: str, params: dict[str, object]) -> object:
            assert method == "textDocument/rename"
            return {
                "changes": {
                    inroot_uri: [
                        {
                            "range": {
                                "start": {"line": 0, "character": 4},
                                "end": {"line": 0, "character": 18},
                            },
                            "newText": "make_invoice",
                        }
                    ]
                }
            }

    server = TensorGrepLSPServer("test", "v1")
    server.provider_mode = "lsp"
    monkeypatch.setattr(
        lsp_module, "_external_client_for_uri", lambda ls, uri, **kwargs: _FakeClient()
    )
    _open_document(server, module_path)

    edit = _workspace_edit_for_symbol(
        server,
        inroot_uri,
        Position(line=0, character=5),
        "make_invoice",
    )

    assert edit is not None
    assert edit.changes is not None
    assert inroot_uri in edit.changes


# --- _uri_to_path edge shapes (audit M3 plan point 5) ----------------------------------


def test_uri_within_root_handles_percent_encoded_segments(tmp_path: Path) -> None:
    # A path with a space round-trips through a percent-encoded file URI and still resolves
    # inside its parent root on every platform.
    target = tmp_path / "repo dir" / "mod.py"
    target.parent.mkdir(parents=True)
    target.write_text("x", encoding="utf-8")
    assert _uri_to_path(_path_to_uri(target)) == target.resolve()
    assert _uri_within_root(_path_to_uri(target), target.parent) is True


def test_uri_to_path_joins_unc_netloc_before_unquoting() -> None:
    # _uri_to_path joins the netloc as //<netloc>/<path> and percent-decodes each segment.
    # Asserted at the string-shape level so the check is not platform-locked to a real UNC share
    # (netloc join + %xx decoding are platform-independent; only real UNC resolution is).
    joined = str(_uri_to_path("file://server/share/dir%20x/f%2Epy")).replace("\\", "/")
    assert "server/share/" in joined
    assert "dir x" in joined
    assert "f.py" in joined


# --- audit-M3 gate findings (independent codex security gate, FIX-BEFORE-MERGE) ---------


def test_uri_to_path_handles_uppercase_file_scheme(tmp_path: Path) -> None:
    """Gate finding 1: the scheme must match case-insensitively. ``FILE:///C:/outside/evil.py``
    previously skipped the ``file://`` prefix test and was resolved as a (drive-relative /
    cwd-relative) filesystem path, so ``_path_within_root`` compared the wrong thing. An
    uppercase-scheme URI must be parsed as a file target and confined like any other URI."""
    root, inside, outside = _inside_and_outside(tmp_path)
    upper_inside = "FILE" + _path_to_uri(inside)[4:]  # scheme case only: FILE:///...
    upper_outside = "FILE" + _path_to_uri(outside)[4:]
    assert upper_inside.startswith("FILE:///")
    assert _uri_to_path(upper_inside) == inside.resolve()
    assert _uri_within_root(upper_inside, root) is True
    assert _uri_within_root(upper_outside, root) is False
    assert (
        _workspace_edit_refused(
            {"documentChanges": [{"kind": "delete", "uri": upper_outside}]}, root
        )
        is True
    )


def test_uri_to_path_refuses_non_file_schemes(tmp_path: Path) -> None:
    """Gate finding 1: a non-file-scheme URI (``http://`` etc.) is NOT a confined local edit
    target — it must be refused fail-closed, never resolved as a cwd-relative local path that
    can land inside the workspace root."""
    root, _, _ = _inside_and_outside(tmp_path)
    for schemed in ("http://evil/inside.py", "https://example.invalid/x.py", "ftp://h/f.py"):
        with pytest.raises(ValueError):
            _uri_to_path(schemed)
        assert _uri_within_root(schemed, root) is False
        assert (
            _workspace_edit_refused({"documentChanges": [{"kind": "delete", "uri": schemed}]}, root)
            is True
        )


def test_uri_to_path_bare_local_paths_still_resolve(tmp_path: Path) -> None:
    """Gate finding 1 control: bare paths (no ``://``) keep their in-process path semantics
    — only strings that LOOK like URIs are scheme-parsed, and only non-file schemes refused."""
    root, inside, _ = _inside_and_outside(tmp_path)
    relative = _uri_to_path("plain/relative/path.py")
    assert relative == Path("plain/relative/path.py").expanduser().resolve()
    assert _uri_to_path(str(inside)) == inside.resolve()
    assert _path_within_root(str(inside), root) is True


def test_fileop_hybrid_textedit_and_fileop_member_is_refused(tmp_path: Path) -> None:
    """Gate finding 2: ONE member carrying BOTH a recognized in-root text-edit AND an
    out-of-root/unknown file-op shape is opaque — neither shape alone — and the whole edit
    is refused."""
    root, inside, outside = _inside_and_outside(tmp_path)
    result = {
        "documentChanges": [
            {
                "textDocument": {"uri": _path_to_uri(inside), "version": None},
                "edits": [{"newText": "x"}],
                "kind": "delete",
                "uri": _path_to_uri(outside),
            }
        ]
    }
    assert _workspace_edit_has_opaque_member(result) is True
    assert _workspace_edit_refused(result, root) is True
    # unknown kind alongside a valid textDocument is also refuse-worthy
    result2 = {
        "documentChanges": [{"textDocument": {"uri": _path_to_uri(inside)}, "kind": "rename"}]
    }
    assert _workspace_edit_has_opaque_member(result2) is True
    assert _workspace_edit_refused(result2, root) is True


def test_fileop_non_list_document_changes_is_refused(tmp_path: Path) -> None:
    """Gate finding 2: a present-but-non-list ``documentChanges`` value cannot be enumerated
    — treat it as opaque and refuse the whole edit instead of passing it as empty."""
    root, _, outside = _inside_and_outside(tmp_path)
    result = {"documentChanges": {"kind": "delete", "uri": _path_to_uri(outside)}}
    assert _workspace_edit_has_opaque_member(result) is True
    assert _workspace_edit_refused(result, root) is True
    result2 = {"documentChanges": "sneaky"}
    assert _workspace_edit_has_opaque_member(result2) is True
    assert _workspace_edit_refused(result2, root) is True


def test_fileop_all_in_root_file_ops_are_not_refused(tmp_path: Path) -> None:
    """Gate finding 3a (confinement level): an ALL-IN-ROOT CreateFile/RenameFile/DeleteFile
    edit is NOT refused by confinement — only out-of-root or opaque members are."""
    root, inside, _ = _inside_and_outside(tmp_path)
    renamed = root / "renamed.py"
    result = {
        "documentChanges": [
            {"kind": "create", "uri": _path_to_uri(inside)},
            {"kind": "rename", "oldUri": _path_to_uri(inside), "newUri": _path_to_uri(renamed)},
            {"kind": "delete", "uri": _path_to_uri(inside)},
        ]
    }
    assert _workspace_edit_has_opaque_member(result) is False
    assert _workspace_edit_refused(result, root) is False


def test_fileop_all_in_root_file_op_edit_is_not_refused_and_still_returns_an_edit(
    tmp_path: Path, monkeypatch
) -> None:
    """Gate finding 3a (end to end): an all-in-root file-op edit is allowed by confinement and
    the rename still returns an edit. The separate LSP-EDIT-CONSTRUCTION mismatch (lsprotocol
    rejects camelCase ``documentChanges`` in ``WorkspaceEdit(**result)``) is explicitly NOT
    fixed here — the confined edit flows through the existing native fallback, which is the
    pre-existing behavior (owner: lsp-change-control)."""
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8"
    )
    module_path = tmp_path / "module.py"
    module_path.write_text("def create_invoice() -> None:\n    return None\n", encoding="utf-8")
    inroot_uri = module_path.resolve().as_uri()

    class _FakeClient:
        def ensure_document(self, **kwargs: object) -> None:
            return None

        def request(self, method: str, params: dict[str, object]) -> object:
            assert method == "textDocument/rename"
            return {"documentChanges": [{"kind": "create", "uri": inroot_uri}]}

    server = TensorGrepLSPServer("test", "v1")
    server.provider_mode = "lsp"
    monkeypatch.setattr(
        lsp_module, "_external_client_for_uri", lambda ls, uri, **kwargs: _FakeClient()
    )
    _open_document(server, module_path)

    # confinement level: the all-in-root file-op edit is NOT refused
    assert (
        _workspace_edit_refused(
            {"documentChanges": [{"kind": "create", "uri": inroot_uri}]}, tmp_path
        )
        is False
    )

    edit = _workspace_edit_for_symbol(
        server,
        inroot_uri,
        Position(line=0, character=5),
        "make_invoice",
    )
    # the rename still answers via the existing fallback; it never carries the provider's
    # file-op (construction is not fixed here — see the LSP-EDIT-CONSTRUCTION comment).
    assert edit is not None
    assert edit.changes is None
    assert all(
        getattr(doc_change, "kind", None) is None for doc_change in (edit.document_changes or [])
    )


# --- audit-M3 gate round 2 (independent codex security gate, FIX-BEFORE-MERGE) -----------


def test_fileop_kind_null_is_opaque_not_a_text_edit(tmp_path: Path) -> None:
    """Gate finding 2a: ``"kind": null`` is a PRESENT kind key, not an absent one. A member
    carrying an explicit null kind alongside a valid textDocument must be opaque (fail closed)
    — the old ``kind is None`` check misclassified it as a text edit."""
    root, inside, _ = _inside_and_outside(tmp_path)
    result = {
        "documentChanges": [
            {
                "kind": None,
                "textDocument": {"uri": _path_to_uri(inside), "version": None},
                "edits": [],
            }
        ]
    }
    assert _workspace_edit_has_opaque_member(result) is True
    assert _workspace_edit_refused(result, root) is True


def test_fileop_snake_case_document_changes_is_refused(tmp_path: Path) -> None:
    """Gate finding 2b: a RAW provider response carrying the snake_case ``document_changes``
    key is opaque. The confinement paths read only the wire form ``documentChanges``, so the
    value can never be enumerated — yet ``WorkspaceEdit(**result)`` accepts that field name
    and would serialize it outbound, an out-of-root file-op bypass."""
    root, _, outside = _inside_and_outside(tmp_path)
    result = {"document_changes": [{"kind": "delete", "uri": _path_to_uri(outside)}]}
    assert _workspace_edit_has_opaque_member(result) is True
    assert _workspace_edit_refused(result, root) is True


def test_uri_to_path_handles_single_slash_file_uri(tmp_path: Path) -> None:
    """Gate finding 3: the RFC-8089 authority-less ``file:/path`` form must be parsed as a
    file URI. Pre-fix it fell through to drive-relative path resolution (``file:/C:/Windows/
    evil`` -> ``C:Windows\\evil`` on Windows, resolving INSIDE the process drive's cwd root),
    so an out-of-root URI could pass confinement and be forwarded to the IDE who applies it
    at the ORIGINAL URI."""
    root, inside, outside = _inside_and_outside(tmp_path)
    single_inside = "file:/" + str(inside).replace("\\", "/")
    single_outside = "file:/" + str(outside).replace("\\", "/")
    assert _uri_to_path(single_inside) == inside.resolve()
    assert _uri_within_root(single_inside, root) is True
    assert _uri_within_root(single_outside, root) is False
    # the escape shape: an out-of-root file:/ URI must never resolve inside the cwd repo root
    assert _uri_within_root("file:/C:/Windows/evil", Path.cwd()) is False
    # and a changes-map edit carrying that URI is refused, not forwarded
    assert (
        _workspace_edit_refused(
            {"changes": {"file:/C:/Windows/evil": [{"newText": "x"}]}}, Path.cwd()
        )
        is True
    )


def test_uri_to_path_drive_paths_are_not_uri_parse(tmp_path: Path) -> None:
    """Gate finding 3 controls: single-letter drive forms stay bare local paths — the scheme
    token regex must exclude ``C:\\``/``c:/`` (any ``[A-Za-z]:[/\\]`` drive prefix) and plain
    filesystem paths with no colon."""
    assert _uri_to_path("C:/local/path") == Path("C:/local/path").expanduser().resolve()
    assert _uri_to_path("c:/local/path") == Path("c:/local/path").expanduser().resolve()
    assert _uri_to_path(str(tmp_path / "repo" / "x.py")) == (tmp_path / "repo" / "x.py").resolve()


# --- audit-M3 gate round 3 (independent codex security gate, NEW HIGH) ------------------
#
# Path-rootless/malformed URI forms: ``file:C:evil``, ``file:relative.py``, a leading-space
# `` file:///C:/Windows/evil``, `` C:/x`` and ``+file://evil/out`` all resolved against the
# server's per-drive CWD and PASSED as in-root, while the ORIGINAL string is forwarded
# unchanged — the path proven safe differs from what the LSP client interprets. External
# targets must now be syntactically valid ABSOLUTE file: URIs (``_valid_external_document_
# uri``); trusted in-process bare-path callers do NOT go through the validator.


def test_uri_to_path_drive_relative_forms_pass_confinement(tmp_path: Path) -> None:
    """Gate round-3 evidence: pre-fix these resolved INSIDE the cwd repo root (refused=False).
    Post-fix the strict validator refuses them all."""
    root = Path.cwd()
    for malformed in (
        "file:C:evil",
        "file:relative.py",
        " file:///C:/Windows/evil",
        " C:/x",
        "+file://evil/out",
    ):
        assert _valid_external_document_uri(malformed) is False, malformed
        assert (
            _workspace_edit_refused({"changes": {malformed: [{"newText": "x"}]}}, root) is True
        ), malformed


def test_external_target_with_decoded_nul_is_refused(tmp_path: Path) -> None:
    """A decoded NUL (``%00``) — or any control character — in the target path is refused:
    POSIX and Windows disagree on NUL-byte paths, and what resolves locally may not be what
    the client applies."""
    _, inside, _ = _inside_and_outside(tmp_path)
    nul_uri = _path_to_uri(inside).replace(inside.name, "in%00.py")
    assert "%00" in nul_uri
    assert _valid_external_document_uri(nul_uri) is False
    assert _workspace_edit_refused({"changes": {nul_uri: [{"newText": "x"}]}}, Path.cwd()) is True
    assert (
        _workspace_edit_refused(
            {"documentChanges": [{"kind": "delete", "uri": nul_uri}]}, Path.cwd()
        )
        is True
    )


def test_external_target_accepts_absolute_file_uri_forms(tmp_path: Path) -> None:
    """Controls: syntactically valid absolute file: URIs — triple-slash and single-slash —
    pass the strict validator and are confined normally."""
    root, inside, outside = _inside_and_outside(tmp_path)
    triple = _path_to_uri(inside)
    single = "file:/" + str(inside).replace("\\", "/")
    assert _valid_external_document_uri(triple) is True
    assert _valid_external_document_uri(single) is True
    assert _workspace_edit_refused({"changes": {triple: []}}, root) is False
    assert (
        _workspace_edit_refused({"documentChanges": [{"kind": "delete", "uri": triple}]}, root)
        is False
    )
    assert (
        _workspace_edit_refused({"documentChanges": [{"kind": "create", "uri": single}]}, root)
        is False
    )
    assert (
        _workspace_edit_refused(
            {
                "changes": {triple: []},
                "documentChanges": [{"kind": "rename", "oldUri": triple, "newUri": single}],
            },
            root,
        )
        is False
    )
    # out-of-root but SYNTACTICALLY valid is refused on confinement, not on form
    assert _workspace_edit_refused({"changes": {_path_to_uri(outside): []}}, root) is True
