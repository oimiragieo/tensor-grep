# PR #883 implemented-diff audit

Target: `main...campaign/2026-08-01-backlog`, limited to Task 1 (PR-C) and Task 2
(PR-D) of `docs/plans/2026-08-01-backlog-campaign.md`. Commits `b4e8e64` and
`ac68e58` were treated as the implementation; Tasks 3/4/5 were deliberately not
audited as missing. No cargo command, routing-parity test, benchmark, or pytest
suite was run.

## Findings

| severity | claim | verdict | file:line | what breaks if shipped |
|---|---|---|---|---|
| MEDIUM | The two CWE-88 tests assert the security property, not a proxy | **FIX-FIRST** — both tests accept a substituted token that no longer names the edited file, and both exercise only one spelling (`-cevil.ini`) | `tests/unit/test_apply_policy.py::test_policy_file_arg_neutralizes_a_dash_leading_relative_path:2127-2151`; `tests/unit/test_apply_policy.py::test_policy_command_instances_never_produces_a_flag_looking_file_token:2154-2200` | A future partial or corrupt fix can leave `--evil`, `-`, or another dash-leading spelling flag-injectable while the security tests remain green; a fix that changes the path to the wrong file also remains green. |
| LOW | The ledger prose pass fixed every stale canonicalization claim and replaced enumeration with derivation | **FIX-FIRST** — `ledger_store.py` still says “PATH scoping (claims subtree only),” and the replacements repeatedly hard-code “five” and/or the five function names | `src/tensor_grep/cli/ledger_store.py` module docstring `:23-24`; `ledger_store.py::_ledger_physical_root:435-441`; `docs/CONTRACTS.md` PATH-scoping contract `:240,253,257-264`; `docs/multi_agent_context_plane.md` ledger section `:148-152` | The shipped architecture text remains internally contradictory today and will become false again as soon as a sixth helper caller is added, despite the PR claiming a derivation-based repair. |
| LOW | The Rust mirror is safe because `validation_template_file_path` “ALWAYS absolutizes” | **FIX-FIRST (comment proof only)** — production is safe, but the stated invariant is false when `current_dir()` fails: the fallback joins against `PathBuf::from(".")` and is relative | `rust_core/src/main.rs::run_validation_command:11045-11053`; `rust_core/src/main.rs::validation_template_file_path:11119-11128` | Runtime CWE-88 protection is not currently lost—the fallback is dot-prefixed—but the new load-bearing security comment certifies a stronger invariant than the code provides and can misguide a later refactor. |

## MEDIUM finding detail

### F1 — the tests prove “not a flag,” but not “the intended path”

The direct test asserts only `file_arg is not None` and
`not file_arg.startswith("-")`. The end-to-end test likewise inspects only the
last parsed token and asserts that it does not start with `-`. Neither assertion
proves that the token still resolves to `dash_file`.

Two concrete wrong implementations therefore pass both tests:

1. Prefixing only the exact fixture value (`relative == "-cevil.ini"`) passes,
   while `--evil.ini` and the file named exactly `-` remain exposed.
2. Returning `x-cevil.ini`, `safe`, or any other non-dash token passes, even
   though the validation command now checks the wrong file.

The test population also omits leading `--`, exactly `-`, dash-leading names
with spaces, an already-`./` spelling, absolute paths, UNC spellings, and the
`{file}` placeholder. The tokenizer loop is real execution and cannot be
satisfied by a comment, but the single no-space fixture hides quoting-dialect
mistakes and does not establish path identity.

Minimum repair: parameterize the path shapes above; exercise `$file` and
`{file}`; construct POSIX and Windows command strings with their respective
quoters; and assert both (a) the parsed token is not flag-looking and (b) the
token resolves to the original edited file (or equals the expected absolute/UNC
path). A deliberately partial mutation must make at least one case red.

## Required audit conclusions

### 1. Security implementation

**Current Python implementation: PASS.**
`apply_policy.py::_policy_file_arg` resolves the candidate, converts an in-root
candidate to a POSIX relative spelling, and prefixes every relative result whose
first character is `-` (`src/tensor_grep/cli/apply_policy.py:484-506`). A
controlled execution of the branch implementation checked `-cevil.ini`,
`--evil.ini`, exactly `-`, `- evil.ini`, an already-`./` input, a nested
`safe/-nested.ini`, a normal path with spaces, an outside-root absolute path,
and a UNC token. POSIX `shlex` and the Windows splitter preserved the intended
single token; every relative token resolved back to its original file.

The prefix is sufficient for both native tokenizers because the resulting
relative argv item begins with `.`, not `-`. Absolute POSIX paths, drive-letter
paths, and UNC paths already begin with a root/drive/network prefix and cannot
be flag-looking. An input spelled with `./` is normalized and receives exactly
one prefix if its normalized relative name begins with `-`. Within the
documented `$file`/`{file}` contract, `./x` and `x` name the same file under the
command cwd; a consumer that treats the placeholder as an opaque label rather
than a path is outside that contract (`docs/CONTRACTS.md:138`).

**No second Python substitution route found.** Every repo-derived edit
`file`/`path`, plus the target-file fallback, passes through
`apply_policy.py::_edited_file_args` and `_policy_file_arg`
(`src/tensor_grep/cli/apply_policy.py:509-533`).
`apply_policy.py::_policy_command_instances` has the only `$file`/`{file}`
replacement in this path (`:536-565`), and its sole production caller is the
command group in `apply_policy` (`:903-910`). The other argv entries remain the
operator-authored command. The absence of a blind `--` in
`apply_policy.py::_run_policy_command` is therefore a sound retirement: an
arbitrary operator-selected program has no universal sentinel position or
semantics.

**Rust production route: PASS, with F3’s comment correction required.** Both
production members in the lint/test loops call
`validation_template_file_path` before passing `file_path` to
`run_validation_command` (`rust_core/src/main.rs:11206-11215,11227-11236`). On
the normal arm it is absolute; on `current_dir()` failure it is dot-prefixed.
Neither arm can start with `-`. The earlier direct calls at `:3679-3866` are
unit-test calls, not production construction sites.

### 2. New tests

The tests are genuine executable arms and, per the supplied revert receipt, do
go red when the whole fix is reverted. They are not satisfiable by the new
comments or their substrings. They nevertheless fail the stronger wrong-fix
oracle described in F1, so the test portion is not approval-grade yet.

### 3. Dead-code deletion

**PASS.** On `main`, `tg callers _classify_lines` returned zero callers. The
same instrument found four callers across three files for the sibling positive
control `_classify_lines_with_metadata`, including its import consumers. An
exact word-boundary tracked-file search across `src`, `tests`, `benchmarks`,
`scripts`, and `rust_core` found only the deleted definition at
`src/tensor_grep/sidecar.py::_classify_lines:157-159`; the sibling control found
the production imports/calls and string monkeypatch references. Exact-string
searches found no registry key, `getattr`, `__all__`, re-export, wildcard
sidecar import, or test reference to `_classify_lines`. Deletion is supported.

### 4. Ledger prose and call-site count

The real count is **exactly five** direct `_ledger_physical_root(path)` calls,
and each member was observed rather than inferred:

- `ledger_store.py::submit_claim:661`
- `ledger_store.py::release_claim:800`
- `ledger_store.py::list_claims:857`
- `ledger_store.py::record_finding:1201`
- `ledger_store.py::find_findings:1338`

Thus the corrected claims that Slice 1 and Slice 2 canonicalize on the same
terms are true. The sixth stale prose site is
`ledger_store.py`’s module docstring at `:23` (“claims subtree only”). The
positive control for the sweep is the now-correct Slice-2 paragraph at
`ledger_store.py:48-57` and the five live call sites above. No additional
current-contract stale site was found after excluding historical plans/audits
and backlog records.

The main CLI docstring change is also factually correct: AST enumeration found
12 calls to `main.py::_emit_scan_incompleteness_banner` and exactly three other
emitters calling `main.py::_completeness_caveat_lines`; the replacement points
readers to the live call sites rather than claiming a fixed command population
(`src/tensor_grep/cli/main.py:11632-11667`).

### 5. Behaviour neutrality

**PASS.** Independently parsing `main` and the PR branch, stripping module,
class, function, and async-function docstrings, and comparing
`ast.dump(..., include_attributes=False)` returned:

```text
src/tensor_grep/cli/main.py NEUTRAL
src/tensor_grep/cli/ledger_store.py NEUTRAL
```

The banner-comment edit is absent from the AST by construction; the changed
docstrings were removed from both arms before comparison.

### 6. Release class

**PASS.** `scripts/validate_pr_title_semver.py::_RELEASE_INTENTS` maps `fix` to
`patch`, `feat` to `minor`, and `chore` to `none` (`:15-25`). This diff now
contains a user-observable security correction in `_policy_file_arg`; leaving
the PR as `chore:` would prevent that correction from publishing. `fix:` is the
correct class. The diff adds no new capability and removes only a private,
zero-caller wrapper, so nothing warrants `feat:` or a breaking marker.

## Verdict

**BLOCK.** The implemented security behavior is sound, the deletion census is
sound, AST neutrality is proven, and `fix:` is correct. Approval is blocked on
three must-fixes: strengthen the two CWE-88 oracles to preserve path identity
and cover the adversarial shape family; remove the remaining stale ledger claim
and replace hard-coded census prose with live derivation; and correct the Rust
comment so its load-bearing proof matches both `current_dir()` arms.

CODEX-PR883-COMPLETE verdict=BLOCK mustfix=3
