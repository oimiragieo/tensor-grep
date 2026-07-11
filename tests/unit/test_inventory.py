"""TDD suite for ``tg inventory`` (round-4 [e], design-council test list)."""

import json

import pytest

import tensor_grep.cli.inventory as inventory_module
from tensor_grep.cli.inventory import build_inventory


def _write(root, rel, content=b"x"):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        content = content.encode("utf-8")
    path.write_bytes(content)
    return path


class TestFailClosedAndBasics:
    def test_empty_repo_returns_zero_totals_not_error(self, tmp_path):
        inv = build_inventory(str(tmp_path))
        assert inv["totals"] == {"files": 0, "bytes": 0}
        assert inv["languages"] == []
        assert inv["scan_limit"]["possibly_truncated"] is False

    def test_nonexistent_path_fails_closed_not_empty_inventory(self, tmp_path):
        missing = tmp_path / "does-not-exist"
        with pytest.raises(FileNotFoundError):
            build_inventory(str(missing))

    def test_single_file_path_returns_one_file_inventory(self, tmp_path):
        f = _write(tmp_path, "solo.py", "print(1)\n")
        inv = build_inventory(str(f))
        assert inv["totals"]["files"] == 1
        assert inv["top_level_dirs"] == []
        assert any(rec["language"] == "python" for rec in inv["languages"])
        # Round-8 audit: a single-file target must NAME the file in largest_files, not report "."
        # (the file IS the root, so relative_to(root) would collapse to ".").
        largest = inv["largest_files"]
        assert largest and largest[0]["path"] == "solo.py"
        assert all(rec["path"] != "." for rec in largest)


class TestWalkExclusions:
    def test_tensor_grep_index_dir_excluded_from_counts(self, tmp_path):
        _write(tmp_path, "real.py", "a=1\n")
        _write(tmp_path, ".tensor-grep/index.db", "SHOULD NOT COUNT")
        _write(tmp_path, ".git/config", "[core]")
        inv = build_inventory(str(tmp_path))
        assert inv["totals"]["files"] == 1  # only real.py

    def test_no_gitignore_present_walks_full_repo(self, tmp_path):
        _write(tmp_path, "a.py")
        _write(tmp_path, "src/b.py")
        _write(tmp_path, "docs/c.md")
        inv = build_inventory(str(tmp_path))
        assert inv["totals"]["files"] == 3

    def test_symlink_loop_does_not_hang_or_double_count(self, tmp_path):
        _write(tmp_path, "real.py", "a=1\n")
        loop = tmp_path / "loop"
        try:
            loop.symlink_to(tmp_path, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation not permitted on this platform")
        inv = build_inventory(str(tmp_path))
        # symlinks are is_dir(follow_symlinks=False)==False -> skipped; real.py counted once.
        assert inv["totals"]["files"] == 1


class TestBinaryHandling:
    def test_binary_file_counted_separately_not_misclassified_as_language(self, tmp_path):
        _write(tmp_path, "blob.py", b"\x00\x01\x02binary")  # .py extension but binary content
        _write(tmp_path, "code.py", "x=1\n")
        inv = build_inventory(str(tmp_path))
        assert inv["binary"]["files"] == 1
        python = next((r for r in inv["languages"] if r["language"] == "python"), None)
        assert python is not None and python["files"] == 1  # only the real .py, not the blob

    def test_binary_file_bytes_included_in_total_but_flagged(self, tmp_path):
        _write(tmp_path, "blob.bin", b"\x00" * 100)
        inv = build_inventory(str(tmp_path))
        assert inv["totals"]["files"] == 1
        assert inv["totals"]["bytes"] == 100
        assert inv["binary"] == {"files": 1, "bytes": 100}
        assert inv["languages"] == []  # binary not classified as a language


class TestClassificationEdges:
    def test_no_extension_file_counted_in_totals_via_basename_fallback(self, tmp_path):
        _write(tmp_path, "Makefile", "all:\n")
        _write(tmp_path, "Dockerfile", "FROM x\n")
        _write(tmp_path, "LICENSE", "MIT\n")
        inv = build_inventory(str(tmp_path))
        assert inv["totals"]["files"] == 3
        labels = {rec["language"] for rec in inv["languages"]}
        assert {"make", "dockerfile", "text"} <= labels

    def test_dotfile_counted_not_silently_excluded(self, tmp_path):
        _write(tmp_path, ".env", "KEY=1\n")
        _write(tmp_path, "app.py", "x=1\n")
        inv = build_inventory(str(tmp_path))
        assert inv["totals"]["files"] == 2  # .env is a file, not silently dropped

    def test_test_file_categorized_as_test_not_code(self, tmp_path):
        _write(tmp_path, "tests/test_thing.py", "def test_x():\n    pass\n")
        _write(tmp_path, "src/thing.py", "x=1\n")
        inv = build_inventory(str(tmp_path))
        cats = {rec["category"]: rec["files"] for rec in inv["categories"]}
        assert cats.get("test") == 1
        assert cats.get("code") == 1


class TestTruncationHonesty:
    def test_truncation_over_max_files_sets_possibly_truncated_and_cause(self, tmp_path):
        for i in range(5):
            _write(tmp_path, f"f{i}.py", "x=1\n")
        inv = build_inventory(str(tmp_path), max_files=3)
        assert inv["scan_limit"]["possibly_truncated"] is True
        assert inv["scan_limit"]["truncation_cause"] == "project-files"
        assert inv["scan_limit"]["scanned_files"] == 3
        assert inv["totals"]["files"] == 3  # counts are a floor

    def test_no_cap_hit_sets_truncation_cause_none(self, tmp_path):
        for i in range(3):
            _write(tmp_path, f"f{i}.py", "x=1\n")
        inv = build_inventory(str(tmp_path), max_files=100)
        assert inv["scan_limit"]["possibly_truncated"] is False
        assert inv["scan_limit"]["truncation_cause"] is None


class TestDeterminism:
    def test_languages_sorted_bytes_desc_then_name(self, tmp_path):
        _write(tmp_path, "big.py", "x" * 1000)
        _write(tmp_path, "small.rs", "y" * 10)
        _write(tmp_path, "mid.go", "z" * 100)
        inv = build_inventory(str(tmp_path))
        langs = [rec["language"] for rec in inv["languages"]]
        assert langs == ["python", "go", "rust"]  # bytes desc

    def test_top_level_dirs_sorted_lexicographically(self, tmp_path):
        _write(tmp_path, "zeta/a.py")
        _write(tmp_path, "alpha/b.py")
        _write(tmp_path, "mid/c.py")
        inv = build_inventory(str(tmp_path))
        dirs = [rec["dir"] for rec in inv["top_level_dirs"]]
        assert dirs == ["alpha", "mid", "zeta"]

    def test_json_output_stable_byte_identical_on_repeat_run(self, tmp_path):
        _write(tmp_path, "a.py", "x=1\n")
        _write(tmp_path, "src/b.rs", "fn main(){}\n")
        _write(tmp_path, "README.md", "# hi\n")
        first = json.dumps(build_inventory(str(tmp_path)), sort_keys=False)
        second = json.dumps(build_inventory(str(tmp_path)), sort_keys=False)
        assert first == second


class TestDeadline:
    # Fix C (2026-07-05): the per-file stat()+_looks_like_binary_file cost (~29s/10k files)
    # dominates over the walk itself (~0.9s/10k). A huge workspace with a large max_files
    # cap could still take minutes even though the cap was never hit. A wall-clock deadline
    # lets the caller bound total time and get an honestly-labeled partial inventory instead.

    @staticmethod
    def _install_advancing_clock(monkeypatch, base=1000.0):
        # Same fake-clock idiom as tests/unit/test_repo_map_deadline.py: monotonic only
        # advances when a file is actually processed, so the deadline crosses deterministically.
        clock = {"t": base}
        monkeypatch.setattr(inventory_module.time, "monotonic", lambda: clock["t"])
        original = inventory_module._looks_like_binary_file

        def _advancing(path):
            clock["t"] += 1.0
            return original(path)

        monkeypatch.setattr(inventory_module, "_looks_like_binary_file", _advancing)
        return clock

    def test_deadline_fires_marks_truncation_cause_and_returns_partial_floor(
        self, tmp_path, monkeypatch
    ):
        for i in range(10):
            _write(tmp_path, f"f{i}.py", "x=1\n")
        self._install_advancing_clock(monkeypatch)

        inv = build_inventory(str(tmp_path), deadline_seconds=2.0)

        assert inv["scan_limit"]["possibly_truncated"] is True
        assert inv["scan_limit"]["truncation_cause"] == "deadline"
        assert inv["totals"]["files"] < 10  # broke early -- a floor, not the real count
        assert inv["scan_limit"]["scanned_files"] == inv["totals"]["files"]

    def test_deadline_binds_over_file_cap_when_it_fires_early(self, tmp_path, monkeypatch):
        # Corrected precedence (dogfood 2026-07-05): when the deadline BREAKS the loop before it
        # processes max_files, STRICTLY FEWER than the cap were scanned, so the time budget is the
        # BINDING constraint -> cause="deadline" even though the walk was also cap-truncated. (Raising
        # --max-repo-files would not help; extending --deadline would.) The earlier "keep
        # project-files" guard mislabeled a real 20s deadline hit on C:/dev/projects as a file-cap.
        for i in range(10):
            _write(tmp_path, f"f{i}.py", "x=1\n")
        self._install_advancing_clock(monkeypatch)

        inv = build_inventory(str(tmp_path), max_files=3, deadline_seconds=2.0)

        assert inv["scan_limit"]["possibly_truncated"] is True
        assert inv["scan_limit"]["truncation_cause"] == "deadline"
        assert inv["totals"]["files"] < 3  # deadline broke it before the 3-file cap

    def test_file_cap_without_firing_deadline_stays_project_files(self, tmp_path):
        # No deadline supplied: a walk larger than max_files stays labeled "project-files".
        for i in range(10):
            _write(tmp_path, f"f{i}.py", "x=1\n")

        inv = build_inventory(str(tmp_path), max_files=3)

        assert inv["scan_limit"]["truncation_cause"] == "project-files"
        assert inv["totals"]["files"] == 3  # processed all cap-limited files, no early break

    def test_no_deadline_supplied_is_unchanged_parity(self, tmp_path):
        for i in range(3):
            _write(tmp_path, f"f{i}.py", "x=1\n")
        inv = build_inventory(str(tmp_path))
        assert inv["scan_limit"]["possibly_truncated"] is False
        assert inv["scan_limit"]["truncation_cause"] is None
        assert inv["totals"]["files"] == 3

    def test_walk_phase_itself_is_deadline_bounded(self, tmp_path, monkeypatch):
        # #52 fix (loop A): previously ONLY the per-file stat()+_looks_like_binary_file loop below
        # was deadline-bounded -- the WALK itself (_iter_repo_files) had no time bound at all, only
        # a max_files COUNT bound (default 50_000), so a slow/huge walk alone could burn the whole
        # --deadline budget before the per-file loop ever got a chance to run (the 76s dogfood gap
        # on `tg inventory --deadline 30`).
        #
        # An "already expired" deadline does NOT distinguish old from new here (a 10-tiny-file walk
        # is fast enough that the OLD code's pre-existing per-file-loop check alone already caught
        # it, producing the same zero-processed outcome either way) -- this needs a clock that
        # advances on every monotonic() call so the WALK's own new per-file check has something
        # real to consume before the per-file loop even starts.
        for i in range(10):
            _write(tmp_path, f"f{i}.py", "x=1\n")
        base = 1000.0
        clock = {"t": base, "calls": 0}

        def _fake_monotonic():
            clock["calls"] += 1
            clock["t"] = base + (clock["calls"] - 1)
            return clock["t"]

        monkeypatch.setattr(inventory_module.time, "monotonic", _fake_monotonic)

        # Call #1 is the `deadline = time.monotonic() + 2.0` line itself (base+0 -> deadline
        # base+2). Before this fix, the WALK made zero monotonic() calls, so the per-file loop's
        # OWN first check (call #2, base+1) would still be under budget and process one file
        # before call #3 (base+2) trips it -- totals.files == 1 pre-fix. After this fix, the
        # walk's OWN per-file check also consumes ticks, pushing the clock past the deadline
        # before the per-file loop gets a single call of its own -- totals.files == 0 post-fix.
        inv = build_inventory(str(tmp_path), deadline_seconds=2.0)

        assert inv["scan_limit"]["possibly_truncated"] is True
        assert inv["scan_limit"]["truncation_cause"] == "deadline"
        assert inv["totals"]["files"] == 0

    def test_walk_phase_deadline_none_is_unaffected(self, tmp_path):
        # Golden-parity guard for the reordered deadline computation: no deadline_seconds means
        # walk_deadline_hit.hit is never set and behavior is byte-identical to before this fix.
        for i in range(10):
            _write(tmp_path, f"f{i}.py", "x=1\n")

        inv = build_inventory(str(tmp_path))

        assert inv["scan_limit"]["possibly_truncated"] is False
        assert inv["scan_limit"]["truncation_cause"] is None
        assert inv["totals"]["files"] == 10

    def test_inventory_deadline_never_zero_when_files_were_discovered(self, tmp_path, monkeypatch):
        # #130(a): live-repro'd bug -- `tg inventory PATH --deadline N` on a large workspace
        # returns totals.files=0 despite the walk having discovered files. ROOT: build_inventory
        # computed ONE deadline shared by the walk phase (_iter_repo_files) and the per-file
        # stat/categorize loop. A walk that (worst case) burns 100% of its allotted budget before
        # returning leaves nothing for the per-file loop -- its very first
        # `time.monotonic() >= deadline` check fires immediately -- totals.files stays 0 even
        # though `walked` is non-empty. This simulates exactly that worst case: the walk consumes
        # whatever deadline it is handed, in full, then still returns discovered files.
        real_files = [_write(tmp_path, f"f{i}.py", "x=1\n") for i in range(5)]

        clock = {"t": 1000.0}
        monkeypatch.setattr(inventory_module.time, "monotonic", lambda: clock["t"])

        def fake_iter_repo_files(
            root, *, max_files=None, deadline_monotonic=None, deadline_hit=None, **kwargs
        ):
            # Worst-case walk: burns 100% of whatever budget it was given before returning,
            # exactly like a huge tree eating the whole --deadline window -- yet still
            # discovers (and returns) real files.
            if deadline_monotonic is not None:
                clock["t"] = deadline_monotonic
            if deadline_hit is not None:
                deadline_hit.hit = True
            return list(real_files)

        monkeypatch.setattr(inventory_module, "_iter_repo_files", fake_iter_repo_files)

        inv = build_inventory(str(tmp_path), deadline_seconds=2.0)

        assert inv["scan_limit"]["possibly_truncated"] is True
        assert inv["scan_limit"]["truncation_cause"] == "deadline"
        # The core bug under test: files WERE discovered (the walk returned a non-empty list)
        # but the per-file loop got zero time to process any of them -- a misleading silent-empty
        # result rather than an honest partial floor.
        assert inv["totals"]["files"] > 0

    def test_inventory_walk_phase_reserves_stat_loop_budget(self, tmp_path, monkeypatch):
        # #130(a): the fix reserves a SLICE of deadline_seconds for phase 2 (the per-file stat
        # loop) by passing a smaller walk_deadline -- not the full deadline -- into
        # _iter_repo_files. Freeze the clock so "the overall deadline" is a known, exact value:
        # a loose "< now + deadline_seconds" bound measured around the call would pass even
        # pre-fix, since some real wall-clock time always elapses during the call itself.
        monkeypatch.setattr(inventory_module.time, "monotonic", lambda: 1000.0)
        seen_deadlines: list[float | None] = []

        def spy_iter_repo_files(root, *, max_files=None, deadline_monotonic=None, **kwargs):
            seen_deadlines.append(deadline_monotonic)
            return []

        monkeypatch.setattr(inventory_module, "_iter_repo_files", spy_iter_repo_files)

        deadline_seconds = 10.0
        build_inventory(str(tmp_path), deadline_seconds=deadline_seconds)

        assert seen_deadlines, "the walker was never called"
        walk_deadline = seen_deadlines[0]
        overall_deadline = 1000.0 + deadline_seconds
        assert walk_deadline is not None
        # Phase 2 must be guaranteed a real, non-zero slice of the budget: the walk's own
        # deadline must be a genuine reservation, not the full overall deadline.
        assert walk_deadline < overall_deadline
        assert walk_deadline > 1000.0  # phase 1 still gets a positive slice, not zero


class TestRegistration:
    def test_cli_default_max_files_matches_module_constant(self):
        import inspect

        from tensor_grep.cli import main as tg_main
        from tensor_grep.cli.inventory import DEFAULT_MAX_INVENTORY_FILES

        default = inspect.signature(tg_main.inventory).parameters["max_repo_files"].default
        # typer.Option returns an OptionInfo whose .default holds the literal value.
        assert default.default == DEFAULT_MAX_INVENTORY_FILES

    def test_inventory_in_known_commands_and_parity_set(self):
        from tensor_grep.cli.commands import KNOWN_COMMANDS

        assert "inventory" in KNOWN_COMMANDS


class TestDeadlineCliWiring:
    # codex review (round-6): the CLI --deadline flag needs acceptance + min-rejection + thread-through
    # coverage, not just build_inventory() behavior.
    def test_inventory_deadline_flag_accepted_and_threaded(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner

        from tensor_grep.cli import main as tg_main

        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
        recorded: dict = {}

        def _spy(path, *, max_files=None, deadline_seconds=None):
            recorded["deadline_seconds"] = deadline_seconds
            return {
                "totals": {"files": 0, "bytes": 0},
                "binary": {"files": 0, "bytes": 0},
                "languages": [],
                "categories": [],
                "top_level_dirs": [],
                "largest_files": [],
                "path": str(path),
                "scan_limit": {"possibly_truncated": False, "truncation_cause": None},
            }

        # The inventory command imports build_inventory from tensor_grep.cli.inventory at call time,
        # so patch it at the source module (not on main, where it isn't a module-level attr).
        monkeypatch.setattr("tensor_grep.cli.inventory.build_inventory", _spy)
        result = CliRunner().invoke(
            tg_main.app, ["inventory", str(tmp_path), "--deadline", "5", "--json"]
        )
        assert result.exit_code == 0, result.output
        assert recorded.get("deadline_seconds") == 5.0

    def test_inventory_deadline_rejects_sub_floor_value(self, tmp_path):
        from typer.testing import CliRunner

        from tensor_grep.cli import main as tg_main

        # min=0.1 -> a sub-floor deadline is a usage error, not a silent 0-budget run.
        result = CliRunner().invoke(
            tg_main.app, ["inventory", str(tmp_path), "--deadline", "0.001"]
        )
        assert result.exit_code == 2

    def test_inventory_exits_2_when_scan_possibly_truncated_json(self, tmp_path, monkeypatch):
        # #130(a) optional bundle: the CLI never inspected scan_limit.possibly_truncated and
        # always exited 0, mirroring neither `map`'s nor the symbol-command exit-2 contract for a
        # truncated (e.g. --deadline-fired) scan. This is agent-observable: a script that only
        # checks $? cannot tell "complete inventory" from "silently truncated inventory".
        from typer.testing import CliRunner

        from tensor_grep.cli import main as tg_main

        def _spy(path, *, max_files=None, deadline_seconds=None):
            return {
                "totals": {"files": 3, "bytes": 30},
                "binary": {"files": 0, "bytes": 0},
                "languages": [],
                "categories": [],
                "top_level_dirs": [],
                "largest_files": [],
                "path": str(path),
                "scan_limit": {
                    "max_files": 50_000,
                    "scanned_files": 3,
                    "possibly_truncated": True,
                    "truncation_cause": "deadline",
                },
            }

        monkeypatch.setattr("tensor_grep.cli.inventory.build_inventory", _spy)
        result = CliRunner().invoke(
            tg_main.app, ["inventory", str(tmp_path), "--deadline", "5", "--json"]
        )
        assert result.exit_code == 2
        # The payload must still be emitted (exit 2 signals "partial", not "nothing produced") --
        # same "output the full payload FIRST, then exit 2" contract as `map`.
        payload = json.loads(result.output)
        assert payload["scan_limit"]["possibly_truncated"] is True

    def test_inventory_exits_0_when_scan_not_truncated_json(self, tmp_path, monkeypatch):
        # Regression pin: a complete (non-truncated) scan must stay exit 0.
        from typer.testing import CliRunner

        from tensor_grep.cli import main as tg_main

        def _spy(path, *, max_files=None, deadline_seconds=None):
            return {
                "totals": {"files": 1, "bytes": 10},
                "binary": {"files": 0, "bytes": 0},
                "languages": [],
                "categories": [],
                "top_level_dirs": [],
                "largest_files": [],
                "path": str(path),
                "scan_limit": {
                    "max_files": 50_000,
                    "scanned_files": 1,
                    "possibly_truncated": False,
                    "truncation_cause": None,
                },
            }

        monkeypatch.setattr("tensor_grep.cli.inventory.build_inventory", _spy)
        result = CliRunner().invoke(tg_main.app, ["inventory", str(tmp_path), "--json"])
        assert result.exit_code == 0, result.output

    def test_inventory_exits_2_when_scan_possibly_truncated_text_mode(self, tmp_path, monkeypatch):
        # The exit-2 gate must fire in text-output mode too, not just --json.
        from typer.testing import CliRunner

        from tensor_grep.cli import main as tg_main

        def _spy(path, *, max_files=None, deadline_seconds=None):
            return {
                "totals": {"files": 3, "bytes": 30},
                "binary": {"files": 0, "bytes": 0},
                "languages": [],
                "categories": [],
                "top_level_dirs": [],
                "largest_files": [],
                "path": str(path),
                "scan_limit": {
                    "max_files": 50_000,
                    "scanned_files": 3,
                    "possibly_truncated": True,
                    "truncation_cause": "deadline",
                },
            }

        monkeypatch.setattr("tensor_grep.cli.inventory.build_inventory", _spy)
        result = CliRunner().invoke(tg_main.app, ["inventory", str(tmp_path), "--deadline", "5"])
        assert result.exit_code == 2
        assert "inventory:" in result.output  # render_inventory_text output still printed
