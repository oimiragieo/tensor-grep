# FIX_PLAN_1_13_17

## Scope

Close the remaining `v1.13.16` dogfood regressions without broadening unrelated
search or agent contracts:

1. `tg search --no-ignore ...` over a real project root must behave like
   `rg --no-ignore ...` for content searches, including ignored generated child
   directories such as `.venv` and `node_modules`.
2. The generated-root safety guard must still protect direct generated roots
   such as `.venv` unless the caller explicitly opts in.
3. Repeated top-level `tg context-render ...` and `tg edit-plan ...` calls should
   use an already-running session daemon and increment `response_cache_*`
   counters instead of bypassing the daemon cache.

## Research Anchors

- Ripgrep documents `--no-ignore` / `-u` as disabling ignore-file filtering while
  keeping hidden and binary filtering separate controls:
  <https://iepathos.github.io/ripgrep/automatic-filtering/>
- Python's cache documentation exposes the same observable contract we need from
  the daemon cache: repeated calls report hits/misses so cache effectiveness can
  be measured: <https://docs.python.org/3/library/functools.html>

## Plan

### `--no-ignore` Content Search

- Change generated-root detection so content searches guard only when the
  requested root itself is generated/cache/dependency, not merely because a
  project root contains generated child directories.
- Keep child-directory detection for file-list style generated-root guardrails.
- Add native front-door tests covering normal and early-rg paths.
- Keep a direct generated-root refusal test.

### Daemon Cache

- If top-level `context-render` / `edit-plan` sees a running daemon and uses the native
  provider, route the request through the daemon.
- Let the daemon create one implicit per-root session for no-session
  `context_render` / `context_edit_plan` requests and reuse that session ID for
  stable response-cache keys.
- Keep stale-session failures falling back to direct top-level context rendering.
- Update `response_cache_scope` wording to include top-level daemon-routed
  requests.

## Out Of Scope

- Making generated/cache roots themselves unguarded by default.
- Auto-starting the daemon for every top-level context request.
- Implementing implicit daemon routing for `tg agent`.
- Claiming raw-search speed parity with `rg`.
