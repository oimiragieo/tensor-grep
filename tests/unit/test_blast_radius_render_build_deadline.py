"""The render deadline must bound the real map build, not only downstream rendering."""

import time

import pytest

from tensor_grep.cli import repo_map


@pytest.mark.parametrize(
    "mode", ["expired-render", "healthy-render", "default-render", "expired-build"]
)
def test_render_deadline_reaches_real_build_and_walk(tmp_path, monkeypatch, mode):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="render-deadline-fixture"\nversion="0.0.0"\n', encoding="utf-8"
    )
    (tmp_path / "sample.py").write_text(
        "def answer():\n    return 42\n\ndef caller():\n    return answer()\n", encoding="utf-8"
    )
    deadline = (
        None
        if mode == "default-render"
        else time.monotonic() + (60 if mode == "healthy-render" else -1)
    )
    real_build, real_walk = repo_map.build_repo_map, repo_map._iter_repo_files
    builds, walks = [], []

    def build(*args, **kwargs):
        result = real_build(*args, **kwargs)
        builds.append((kwargs.get("deadline_monotonic"), result))
        return result

    def walk(*args, **kwargs):
        result = real_walk(*args, **kwargs)
        walks.append((kwargs.get("deadline_monotonic"), result))
        return result

    monkeypatch.setattr(repo_map, "build_repo_map", build)
    monkeypatch.setattr(repo_map, "_iter_repo_files", walk)
    if mode == "expired-build":
        result = repo_map.build_repo_map(tmp_path, max_repo_files=10, deadline_monotonic=deadline)
    else:
        result = repo_map.build_symbol_blast_radius_render(
            "answer", tmp_path, max_repo_files=10, deadline_monotonic=deadline
        )

    assert len(builds) == 1
    if mode.startswith("expired"):
        assert builds[0][0] == deadline, "deadline dropped before real build"
        assert walks and all(value == deadline for value, _paths in walks)
        assert all(not paths for _value, paths in walks)
        assert builds[0][1]["deadline_limit"]["files_scanned"] == 0
        assert builds[0][1]["symbols"] == []
    else:
        assert builds[0][1]["symbols"]
        assert result["definitions"]
        assert not result.get("partial")
        if mode == "default-render":
            assert builds[0][0] is None
            assert walks and all(value is None for value, _paths in walks)
