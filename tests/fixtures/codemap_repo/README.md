# Codemap Fixture Repo

A tiny fixture repository used by the `tg codemap` test suite. It exercises Python, TypeScript,
and Rust symbol extraction plus the folder blurb fallback chain (README -> `__init__.py` ->
generic).

Nothing in this directory is meant to run; it exists purely as deterministic input for
`tests/unit/test_codemap.py` and `tests/unit/test_codemap_freshness.py`.
