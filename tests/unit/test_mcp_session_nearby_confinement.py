"""CLI nearby lookup cannot expand an MCP client's session authorization root."""

import json

import pytest


@pytest.fixture(scope="module")
def authenticated_outer_session(tmp_path_factory):
    from tensor_grep.cli import mcp_server

    outer = tmp_path_factory.mktemp("session-nearby") / "outer"
    outer.mkdir()
    (outer / ".git").mkdir()
    (outer / "outer_only.py").write_text(
        "def outer_private_symbol():\n    return 1\n", encoding="utf-8"
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("TG_MCP_ROOT", str(outer))
        patch.delenv("TG_SESSION_NEARBY_LOOKUP", raising=False)
        opened = json.loads(mcp_server.tg_session_open(path=str(outer), max_repo_files=10))
        assert not opened.get("error"), opened
        session_id = opened["session_id"]
        shown = json.loads(mcp_server.tg_session_show(session_id=session_id, path=str(outer)))
        assert not shown.get("error"), shown
        assert "outer_private_symbol" in json.dumps(shown)
    nested = outer / "nested"
    nested.mkdir()
    (nested / ".git").mkdir()
    (nested / "nested.py").write_text("def nested_symbol(): pass\n", encoding="utf-8")
    return outer, nested, session_id


_ID_ACTIONS = [
    "show",
    "refresh",
    "context",
    "edit_plan",
    "context_render",
    "blast_radius",
    "blast_radius_plan",
    "blast_radius_render",
    "file_importers",
]


@pytest.mark.parametrize("action", _ID_ACTIONS)
@pytest.mark.parametrize("direct", [True, False], ids=["legacy", "meta"])
def test_nearby_authenticated_session_outside_mcp_root_is_refused(
    authenticated_outer_session,
    monkeypatch,
    action,
    direct,
):
    from tensor_grep.cli import mcp_server

    _outer, nested, session_id = authenticated_outer_session
    monkeypatch.chdir(nested)
    monkeypatch.setenv("TG_MCP_ROOT", str(nested))
    monkeypatch.setenv("TG_SESSION_NEARBY_LOOKUP", "1")
    kwargs = {"session_id": session_id, "path": str(nested)}
    if action in {"context", "edit_plan", "context_render"}:
        kwargs["query"] = "outer_private_symbol"
    if action.startswith("blast_radius"):
        kwargs["symbol"] = "outer_private_symbol"
    if action == "file_importers":
        kwargs["file"] = str(nested / "nested.py")
    tool = getattr(mcp_server, "tg_session_" + action) if direct else mcp_server.tg_session
    if not direct:
        kwargs["action"] = action
    payload = json.loads(tool(**kwargs))
    assert payload.get("error"), payload
    assert "must stay within" in str(payload["error"]), payload


def test_nearby_disabled_refuses_missing_nested_session(authenticated_outer_session, monkeypatch):
    from tensor_grep.cli import mcp_server

    _outer, nested, session_id = authenticated_outer_session
    monkeypatch.setenv("TG_MCP_ROOT", str(nested))
    monkeypatch.delenv("TG_SESSION_NEARBY_LOOKUP", raising=False)
    payload = json.loads(mcp_server.tg_session_show(session_id=session_id, path=str(nested)))
    assert payload.get("error"), payload


@pytest.mark.parametrize("nearby", [False, True], ids=["equal", "in-root-nearby"])
def test_authenticated_session_inside_mcp_root_remains_accessible(
    authenticated_outer_session,
    monkeypatch,
    nearby,
):
    from tensor_grep.cli import mcp_server

    outer, nested, session_id = authenticated_outer_session
    monkeypatch.setenv("TG_MCP_ROOT", str(outer))
    monkeypatch.setenv("TG_SESSION_NEARBY_LOOKUP", "1")
    payload = json.loads(
        mcp_server.tg_session(
            action="show", session_id=session_id, path=str(nested if nearby else outer)
        )
    )
    assert not payload.get("error"), payload
    assert "outer_private_symbol" in json.dumps(payload)


def test_cli_nearby_lookup_remains_available_outside_mcp_root(
    authenticated_outer_session, monkeypatch
):
    from tensor_grep.cli import session_store

    outer, nested, session_id = authenticated_outer_session
    monkeypatch.setenv("TG_MCP_ROOT", str(nested))
    monkeypatch.setenv("TG_SESSION_NEARBY_LOOKUP", "1")
    payload = session_store.get_session(session_id, str(nested))
    assert payload["root"] == str(outer)
    assert "outer_private_symbol" in json.dumps(payload)
