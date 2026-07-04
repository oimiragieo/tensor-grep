"""Round-6 rank-4: the MCP context tools (the surface agents actually call) must bound the context
payload by default, like the CLI (#359). They defaulted to max_tokens=None (unbounded) -> a pack/
render could balloon straight into a model prompt."""

import json

from tensor_grep.cli import mcp_server, repo_map


def test_mcp_context_cap_constant_mirrors_the_library_constant():
    # A drift guard: the MCP-surface literal must equal repo_map's canonical default.
    assert mcp_server._DEFAULT_MCP_CONTEXT_MAX_TOKENS == repo_map._DEFAULT_CONTEXT_MAX_TOKENS


def _project(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "payments.py").write_text(
        "def create_invoice():\n    return 1\n", encoding="utf-8"
    )
    return str(tmp_path)


def test_tg_context_render_bounds_by_default(tmp_path):
    payload = json.loads(mcp_server.tg_context_render("create invoice", _project(tmp_path)))
    assert payload["max_tokens"] == mcp_server._DEFAULT_MCP_CONTEXT_MAX_TOKENS


def test_tg_context_render_zero_is_unbounded_opt_out(tmp_path):
    payload = json.loads(
        mcp_server.tg_context_render("create invoice", _project(tmp_path), max_tokens=0)
    )
    assert payload["max_tokens"] is None  # normalized <=0 -> unbounded


def test_tg_context_pack_accepts_and_defaults_max_tokens(tmp_path):
    # tg_context_pack had NO max_tokens param at all -> always unbounded. It now accepts one and
    # defaults to the bound (call succeeds + emits the pack).
    out = mcp_server.tg_context_pack("create invoice", _project(tmp_path))
    payload = json.loads(out)
    assert isinstance(payload, dict)  # bounded call still returns a valid pack
