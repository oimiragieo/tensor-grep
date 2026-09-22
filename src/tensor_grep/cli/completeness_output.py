"""Render display-pagination and completeness caveats for CLI surfaces."""

from __future__ import annotations

from typing import Any


def _output_limit_note(payload: dict[str, Any]) -> str | None:
    """Describe exact display omissions without classifying the analysis as incomplete."""
    limit = payload.get("output_limit")
    if not isinstance(limit, dict):
        return None

    def _omitted(flag_key: str, total_key: str, returned_key: str, omitted_key: str) -> int | None:
        if not limit.get(flag_key):
            return None
        if limit.get(omitted_key) is not None:
            return max(0, int(limit[omitted_key]))
        return max(0, int(limit.get(total_key, 0)) - int(limit.get(returned_key, 0)))

    dropped: list[str] = []
    knobs: set[str] = set()
    callers_omitted = _omitted(
        "callers_truncated", "total_callers", "returned_callers", "omitted_callers"
    )
    if callers_omitted:
        dropped.append(f"{callers_omitted} caller(s)")
        knobs.add("--max-callers")
    files_omitted = _omitted("files_truncated", "total_files", "returned_files", "omitted_files")
    if files_omitted:
        dropped.append(f"{files_omitted} file(s)")
        knobs.add("--max-files")
    tests_omitted = _omitted("tests_truncated", "total_tests", "returned_tests", "omitted_tests")
    tests_knob = (
        "--max-files" if "max_files" in limit else "--max-tests" if "max_tests" in limit else None
    )
    if tests_omitted and tests_knob is not None:
        dropped.append(f"{tests_omitted} test file(s)")
        knobs.add(tests_knob)
    consumers_omitted = _omitted(
        "import_consumers_truncated",
        "total_import_consumers",
        "returned_import_consumers",
        "omitted_import_consumers",
    )
    if consumers_omitted:
        dropped.append(f"{consumers_omitted} import consumer(s)")
        knobs.add("--max-files")
    if not dropped and limit.get("possibly_truncated"):
        map_files_omitted = max(
            0,
            int(
                limit.get(
                    "omitted_files",
                    int(limit.get("original_files", 0)) - int(limit.get("emitted_files", 0)),
                )
            ),
        )
        map_tests_omitted = max(
            0,
            int(
                limit.get(
                    "omitted_tests",
                    int(limit.get("total_tests", 0)) - int(limit.get("returned_tests", 0)),
                )
            ),
        )
        if map_files_omitted:
            dropped.append(f"{map_files_omitted} file(s)")
            knobs.add("--max-files")
        if map_tests_omitted:
            dropped.append(f"{map_tests_omitted} test file(s)")
            knobs.add("--max-files")
    if not dropped:
        return None
    knob_text = "--max-callers/--max-files" if len(knobs) == 2 else knobs.pop()
    return f"OUTPUT LIMITED: display omitted {' and '.join(dropped)}; raise {knob_text} to see more"


def _completeness_caveat_lines(
    caveat: str | None, *, is_truncation: bool
) -> tuple[str | None, str | None]:
    """Return a leading warning for incompleteness or a trailing pagination note."""
    if caveat is None:
        return None, None
    if is_truncation:
        return f"warning: {caveat}", None
    return None, f"note: {caveat}"
