# Investigation evidence

`tg find QUERY PATH` and `tg agent PATH QUERY` accept `--grounding off|local|registry`.
The default, `local`, reads bounded manifests and locks in the selected directory without
executing project code. A single-file selection uses its containing directory. It does not
search parent directories for a different project or walk every workspace manifest.

Python `pyproject.toml`, `uv.lock`, and `poetry.lock`; JavaScript `package.json` and
`package-lock.json`; and Rust `Cargo.toml` and `Cargo.lock` supply constraints and resolved
version provenance. Multiple locked versions remain ambiguous. A lockfile version is
installation intent, not proof that the environment installed it. Unsupported locks and
workspace inheritance do not establish a resolved version.

Each file is capped at 256 KiB; at most 32 dependencies are reported. JSON/MCP
`dependency_grounding` reports malformed or unreadable inputs, omissions, import ambiguity,
and unavailable API evidence. Available local `.pyi` or `index.d.ts` declarations are static
evidence. Package names do not prove import identity, and no grounding mode proves API
compatibility. Output uses the remaining token budget; omitted dependencies remain counted.

`registry` explicitly permits up to four HTTPS metadata requests to PyPI, npm, or crates.io.
Requests run in an isolated interpreter with no proxy inheritance or redirects, capped
responses, and a shared deadline of at most five seconds. Dated receipts are cached for
24 hours in the per-user `tensor-grep/registry-grounding-v1` cache. Cached metadata is
caller writable advisory evidence and is labeled accordingly. Unavailable requests are
reported individually. This mode can create receipt cache files but never installs packages.

`tg agent PATH QUERY --plan-hops --json` adds `investigation_hops` without making network
requests or changing files. The graph has at most three stages: declarations, supported
caller/import evidence, and associated tests. It reuses the existing map and capsule
evidence under the same deadline and scan budget; it does not perform a second hop scan.
Only supported edges are included: a test may connect directly to a declaration when
no supported intermediate caller edge exists. Filename similarity alone does not create
a test edge. The graph reports ambiguity, omissions, and partial coverage; missing edges
do not prove missing callers or tests. Line-addressed calls remain distinct from graph
associations, and the capsule's target confidence and clarification requirements remain active.

`--rerank off|auto|cross-encoder` defaults to `off`. `find` reorders the existing candidate
head; `agent` reorders snippets as advisory output while retaining the selected target,
line maps, and candidate membership. See [experimental features](EXPERIMENTAL.md) for
installation and execution details. External process timeouts remain appropriate for automation.

MCP contract `1.16.0` exposes `grounding` and `rerank` through `tg_find` and
`tg_query(action="find")`. `tg_agent_capsule` and `tg_context(action="capsule")` also
accept Boolean `plan_hops`. Legacy discovery remains available.
