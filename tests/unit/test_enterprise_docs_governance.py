from pathlib import Path

README_PATH = Path("README.md")
CONTRIBUTING_PATH = Path("CONTRIBUTING.md")
SUPPORT_MATRIX_PATH = Path("docs/SUPPORT_MATRIX.md")
CONTRACTS_PATH = Path("docs/CONTRACTS.md")
HOTFIX_PROCEDURE_PATH = Path("docs/HOTFIX_PROCEDURE.md")
INSTALLATION_PATH = Path("docs/installation.md")
RELEASE_CHECKLIST_PATH = Path("docs/RELEASE_CHECKLIST.md")
CI_PIPELINE_PATH = Path("docs/CI_PIPELINE.md")
EXPERIMENTAL_PATH = Path("docs/EXPERIMENTAL.md")
DOCS_INDEX_PATH = Path("docs/index.md")
TOOL_COMPARISON_PATH = Path("docs/tool_comparison.md")
MKDOCS_PATH = Path("mkdocs.yml")
RESIDENT_WORKER_RUNBOOK_PATH = Path("docs/runbooks/resident-worker.md")
GPU_RUNBOOK_PATH = Path("docs/runbooks/gpu-troubleshooting.md")
CACHE_RUNBOOK_PATH = Path("docs/runbooks/cache-management.md")
SECURITY_PATH = Path("SECURITY.md")


def test_readme_should_point_to_enterprise_contract_docs() -> None:
    readme = README_PATH.read_text(encoding="utf-8")

    assert "docs/CI_PIPELINE.md" in readme
    assert "docs/SUPPORT_MATRIX.md" in readme
    assert "docs/CONTRACTS.md" in readme
    assert "docs/HOTFIX_PROCEDURE.md" in readme
    assert "docs/EXPERIMENTAL.md" in readme
    assert "## Future Work" in readme


def test_support_matrix_should_distinguish_ci_tested_from_best_effort() -> None:
    doc = SUPPORT_MATRIX_PATH.read_text(encoding="utf-8")

    assert "CI-tested" in doc
    assert "Best-effort" in doc
    assert "3.11" in doc
    assert "3.12" in doc
    assert "Python < 3.11" in doc
    assert "3.9" not in doc
    assert "3.10" not in doc
    assert "3.13" not in doc
    assert "3.14" not in doc
    assert "Apple Silicon" in doc
    assert "docs/EXPERIMENTAL.md" in doc


def test_contracts_should_exclude_experimental_surface_from_stable_guarantees() -> None:
    doc = CONTRACTS_PATH.read_text(encoding="utf-8")

    assert "docs/EXPERIMENTAL.md" in doc
    assert "tg worker" in doc
    assert "TG_RESIDENT_AST" in doc
    assert "not covered by the stability guarantees" in doc


def test_installation_doc_should_describe_release_validated_channels() -> None:
    doc = INSTALLATION_PATH.read_text(encoding="utf-8")

    assert "Recommended Channel by Use Case" in doc
    assert "tg update" in doc
    assert "tg-windows-amd64-cpu.exe" in doc
    assert "tg-linux-amd64-cpu" in doc
    assert "tg-macos-amd64-cpu" in doc
    assert "docs/EXPERIMENTAL.md" in doc


def test_hotfix_procedure_should_route_through_semantic_release() -> None:
    doc = HOTFIX_PROCEDURE_PATH.read_text(encoding="utf-8")

    assert "Do not manually create release tags" in doc
    assert "semantic-release" in doc
    assert "fix: correct <hotfix subject>" in doc
    assert "vX.Y.Z" in doc
    assert "vX.Y.(Z+1)" in doc


def test_contributing_should_match_semantic_release_flow() -> None:
    doc = CONTRIBUTING_PATH.read_text(encoding="utf-8")

    assert "semantic-release" in doc
    assert "Do not manually create release tags" in doc
    assert "`feat: ...` => minor release" in doc
    assert "`fix: ...` or `perf: ...` => patch release" in doc


def test_release_checklist_should_define_enterprise_ready_evidence() -> None:
    doc = RELEASE_CHECKLIST_PATH.read_text(encoding="utf-8")

    assert "## 0. Enterprise-ready evidence" in doc
    assert "SBOMs" in doc
    assert "provenance" in doc
    assert "docs/SUPPORT_MATRIX.md" in doc
    assert "docs/EXPERIMENTAL.md" in doc


def test_ci_pipeline_doc_should_explain_release_and_supply_chain_automation() -> None:
    doc = CI_PIPELINE_PATH.read_text(encoding="utf-8")

    assert "Semantic Release" in doc
    assert "benchmark.yml" in doc
    assert "Benchmarks" in doc
    assert "Security Audit" in doc
    assert "Dependabot" in doc
    assert "auto-merge only for low-risk updates" in doc
    assert "[Security Audit] Scheduled dependency audit failure" in doc
    assert "scripts/validate_release_assets.py" in doc


def test_security_doc_should_exist_when_readme_links_to_it() -> None:
    readme = README_PATH.read_text(encoding="utf-8")

    assert "[SECURITY.md](SECURITY.md)" in readme
    assert SECURITY_PATH.exists()


def test_docs_index_should_point_to_current_product_contracts() -> None:
    doc = DOCS_INDEX_PATH.read_text(encoding="utf-8")

    assert "native search and rewrite tool" in doc
    assert "Rust-native CPU text search" in doc
    assert "docs/CI_PIPELINE.md" in doc
    assert "docs/benchmarks.md" in doc
    assert "docs/tool_comparison.md" in doc
    assert "docs/SUPPORT_MATRIX.md" in doc
    assert "GPU acceleration is benchmark-governed" in doc


def test_tool_comparison_doc_should_keep_workload_specific_claims() -> None:
    doc = TOOL_COMPARISON_PATH.read_text(encoding="utf-8")

    assert "single universal winner" in doc
    assert "one benchmark is never enough" in doc
    assert "Host-Local Command Snapshot" in doc
    assert "Semgrep" in doc
    assert "Zoekt" in doc
    assert "git grep --no-index" in doc


def test_mkdocs_should_publish_current_repo_and_enterprise_nav() -> None:
    doc = MKDOCS_PATH.read_text(encoding="utf-8")

    assert "Native search and rewrite tool" in doc
    assert "https://github.com/oimiragieo/tensor-grep" in doc
    assert "CI Pipeline: CI_PIPELINE.md" in doc
    assert "Support Matrix: SUPPORT_MATRIX.md" in doc
    assert "Contracts: CONTRACTS.md" in doc
    assert "Experimental Features: EXPERIMENTAL.md" in doc
    assert "Tool Comparison: tool_comparison.md" in doc


def test_experimental_docs_and_runbooks_should_warn_about_worker_support_boundary() -> None:
    experimental = EXPERIMENTAL_PATH.read_text(encoding="utf-8")
    runbook = RESIDENT_WORKER_RUNBOOK_PATH.read_text(encoding="utf-8")

    assert "Not covered by the stable enterprise contract" in experimental
    assert "workload-dependent" in experimental
    assert "not part of the stable default enterprise surface" in runbook
    assert "tg worker --stop" in runbook


def test_experimental_docs_should_list_runtime_env_flags() -> None:
    experimental = EXPERIMENTAL_PATH.read_text(encoding="utf-8")

    assert "TG_FORCE_CPU=1" in experimental
    assert "TG_RUST_EARLY_POSITIONAL_RG=1" in experimental
    assert "TG_RESIDENT_AST=1" in experimental


def test_operational_runbooks_should_include_windows_safe_commands() -> None:
    gpu = GPU_RUNBOOK_PATH.read_text(encoding="utf-8")
    cache = CACHE_RUNBOOK_PATH.read_text(encoding="utf-8")

    assert '$env:TG_FORCE_CPU = "1"' in gpu
    assert "Remove-Item -LiteralPath .tg_cache -Recurse -Force" in cache


NATIVE_SEARCH_RS = Path("rust_core/src/native_search.rs")
MAIN_RS = Path("rust_core/src/main.rs")
REPO_MAP_PY = Path("src/tensor_grep/cli/repo_map.py")

# The bullet that RECORDS the retracted wording. The stale phrases legitimately appear inside it
# and must not be searched for there -- quoting a retraction is the opposite of asserting it.
_RETRACTION_MARKER = "PREVIOUS TEXT, recorded because deleting a retracted claim silently"


def test_contracts_native_json_incompleteness_claims_match_the_rust_source() -> None:
    """#318: CONTRACTS.md described the native `--json` route as silently partial. It is not.

    For several releases this doc told agents the compiled engine "does NOT emit
    `incomplete_reason_class`" and "exits `0` where `rg` ... exits `2`". Task #276 made both
    false, and nothing failed -- the paragraph was pinned by no test at all, which is why it
    survived the change that invalidated it. A contract doc that is wrong in the SAFE direction
    is merely stale; this one was wrong in the UNSAFE direction, telling a caller to distrust a
    disclosure that is now present and to expect exit 0 where the binary now exits 2.

    So this pins the doc against the RUST SOURCE rather than against itself. Both arms can fail:
    remove the emission or the exit gate and the premise assertions fail pointing at the Rust;
    let the prose drift back and the claim assertions fail pointing at the doc. A test that only
    grepped the doc for a phrase would pass just as happily against a native engine that had
    silently regressed.
    """
    native_rs = NATIVE_SEARCH_RS.read_text(encoding="utf-8")
    main_rs = MAIN_RS.read_text(encoding="utf-8")
    contracts = CONTRACTS_PATH.read_text(encoding="utf-8")

    # PREMISE -- the behaviour the doc now claims must actually be in the source. Without these,
    # the assertions below would keep passing over a native engine that had stopped disclosing,
    # and the doc would be "correct" about a fiction.
    assert "incomplete_reason_class:" in native_rs, (
        "the native --json envelope no longer emits incomplete_reason_class; CONTRACTS.md's "
        "claim that it does is now the wrong half of this pair -- fix the Rust, or the doc"
    )
    assert "incomplete_paths_count:" in native_rs
    assert "walk_errors" in native_rs
    assert "std::process::exit(2)" in main_rs, (
        "the native exit-2-on-incomplete gate is gone; CONTRACTS.md promises rg-parity here"
    )

    # THE CLAIM -- the doc must state the current behaviour, with the citations a reader needs
    # to re-verify it rather than trust this prose.
    assert "`native_search.rs:2489-2491`" in contracts
    assert "`main.rs:8388`" in contracts

    # THE RETRACTION -- the stale phrases may appear ONLY inside the bullet that records them as
    # withdrawn. Anywhere else they are being asserted again.
    assert _RETRACTION_MARKER in contracts, (
        "the retraction record was deleted; a silently removed claim is one the next reader "
        "re-derives from scratch"
    )
    head, _, tail = contracts.partition(_RETRACTION_MARKER)
    retraction_end = tail.index("\n  - WHAT REMAINS TRUE")
    outside_the_retraction = head + tail[retraction_end:]
    for stale in (
        "does NOT emit `incomplete_reason_class`",
        "exits `0` where `rg` on the same unreadable directory exits `2`",
        "nobody in this development environment can compile Rust",
    ):
        assert stale not in outside_the_retraction, (
            f"CONTRACTS.md asserts the retracted claim {stale!r} outside the retraction record; "
            "task #276 made it false and Rust is compiled in CI"
        )


def test_contracts_does_not_still_exclude_the_two_routes_that_have_landed() -> None:
    """The same bullet's OTHER claim rotted, and this test is why it was allowed to.

    The test above pins three retracted phrases. It did not pin the sentence beside them --
    "Two routes still discard the count ... the multi-pattern `-e`/`-f` JSON route (task 317) and
    the CUDA-gated `gpu_native.rs` twin (task 316)" -- so when #811 and #823 landed those exact
    routes, the doc went on telling agents to distrust two surfaces that had been fixed. That is
    plan Task 10's finding reproducing itself INSIDE the bullet it was written about: pinning
    some claims of a multi-claim paragraph leaves the rest free to rot, and the unpinned ones are
    exactly where drift lands.

    Pinned against the RUST SOURCE in both directions, like its sibling: remove the emission and
    the premise fails pointing at the Rust; let the exclusion return and the claim fails pointing
    at the doc.
    """
    main_rs = MAIN_RS.read_text(encoding="utf-8")
    contracts = CONTRACTS_PATH.read_text(encoding="utf-8")

    # PREMISE -- both envelopes really do carry the triple now. Cited by SYMBOL: line numbers in
    # prose about a file that keeps changing rot by construction (the #764 receipt).
    for struct_name in ("struct SearchResultJson", "struct GpuNativeSearchResultJson"):
        assert struct_name in main_rs, f"{struct_name} is gone; re-derive this pin before editing"
        body = main_rs.split(struct_name, 1)[1].split("\n}", 1)[0]
        for field in ("result_incomplete", "incomplete_reason_class", "incomplete_paths_count"):
            assert field in body, (
                f"{struct_name} no longer carries {field}; CONTRACTS.md now claims it does, so "
                "either the Rust regressed or the doc is the wrong half of this pair"
            )

    # THE CLAIM -- the doc must not still be excluding them. Checked outside the retraction
    # record, which legitimately quotes withdrawn wording.
    head, _, tail = contracts.partition(_RETRACTION_MARKER)
    outside_the_retraction = head + tail[tail.index("\n  - WHAT REMAINS TRUE") :]
    assert "still discard the count" not in outside_the_retraction, (
        "CONTRACTS.md still excludes the multi-pattern and gpu_native routes from the "
        "incomplete_reason_class allow-list; both landed (#811 task 317, #823 task 316)"
    )


def test_contracts_incomplete_paths_count_is_documented_as_an_event_count() -> None:
    """Task 320: the field is named `..._paths_count` and does NOT count distinct paths.

    An external dogfood read the name and inferred "how many places could I not look". Measured
    on the v1.99.5 release binary against ONE ACL-denied directory: passing the same root twice
    yields `2`. `build_walk_builder` adds every root without deduplicating, so an overlapping
    root (`tg search PAT . src` -- routine for agents) walks the subtree twice and the same
    denied directory raises the count twice. `rg` prints its access-denied line per visit too,
    so the number is a faithful count of failed reads, not a defect.

    The fix was DOCUMENTATION, not a rename: the field shipped in v1.99.5 and is contract-
    documented, so `docs/SUPPORT_MATRIX.md` binds it to >=90 days AND >=2 minor versions of
    DEPRECATED marking before removal. Renaming would be a 90-day dual-emit exercise that
    doubles the field surface this campaign exists to make legible.

    Pinned against the SOURCE, not against itself, so both arms can fail: add dedup to the
    root loop and the premise assertion fires pointing at the Rust (the doc's caveat would then
    be describing behaviour that no longer exists); let the prose drift back to "how many paths"
    and the claim assertions fire pointing at the doc.
    """
    native_rs = NATIVE_SEARCH_RS.read_text(encoding="utf-8")
    contracts = CONTRACTS_PATH.read_text(encoding="utf-8")

    # PREMISE -- roots are still added WITHOUT dedup, which is the whole reason one distinct
    # path can raise the count more than once. If this ever gains dedup, the caveat below
    # becomes false and must be revisited rather than left as stale reassurance.
    assert "for root in roots.iter().skip(1)" in native_rs, (
        "build_walk_builder no longer iterates the extra roots as-is; the CONTRACTS caveat "
        "that overlapping roots double-count may now be wrong -- re-measure before trusting it"
    )
    assert "builder.add(root);" in native_rs

    # THE CLAIM -- the doc must say EVENTS, and must warn the name is misleading.
    assert "walk-error EVENTS" in contracts
    assert "it is NOT a count of distinct paths" in contracts

    # The old, wrong phrasing must not survive anywhere.
    assert "-- how many paths the walk could not read" not in contracts, (
        "the pre-task-320 phrasing is back; it tells a caller the field answers a question "
        "it does not answer"
    )


def test_contracts_says_result_incomplete_alone_is_not_a_completeness_check() -> None:
    """Task 328/333: an OUTPUT cap is contract-legal but was UNDISCOVERABLE.

    An external reviewer with full repo access, holding the JSON, still concluded the tool
    claimed completeness: `tg callers . <symbol> --json` can return 1 of 4 callers with
    `result_incomplete: false`, `not_found: false`, and exit 0. Every one of those is correct
    per the OUTPUT-cap-stays-exit-0 rule -- the analysis DID run to completion and only the
    rendering was capped -- so the fix was never to flip the flag or the exit code (that
    relitigates task 294 and breaks the pins). The fix was to name the field a caller must
    branch on instead. This test exists because that fix shipped as documentation ONLY
    (4195cbf / PR #815: one file, ten insertions, zero tests), which is the exact shape of
    task 318 -- a contract paragraph nothing fails when it drifts.

    Pinned against the SOURCE so both arms can fail: delete the paragraph and the claim
    assertions fire; change `_apply_symbol_token_budget` to stop emitting `token_budget` on a
    complete result and the premise fires, because the doc's load-bearing warning is precisely
    that the object's PRESENCE proves nothing.
    """
    repo_map = REPO_MAP_PY.read_text(encoding="utf-8")
    contracts = CONTRACTS_PATH.read_text(encoding="utf-8")

    # PREMISE -- `_apply_symbol_token_budget` still stamps `token_budget` on BOTH paths: the
    # under-budget path (`primary_truncated: False`) and the capped path. That asymmetry with
    # `result_incomplete` (omitted when complete) is the whole reason the doc has to spell out
    # per-field presence rules. If the complete-result branch ever goes away, the paragraph
    # becomes wrong and must be rewritten rather than left as stale reassurance.
    assert "def _apply_symbol_token_budget(" in repo_map
    assert '"primary_truncated": False,' in repo_map, (
        "_apply_symbol_token_budget no longer emits token_budget on a COMPLETE result; the "
        "CONTRACTS paragraph that the object's presence proves nothing is now wrong"
    )
    assert '"primary_truncated": primary_truncated,' in repo_map

    # THE CLAIM -- the doc must say the flag is insufficient AND name the replacement field.
    assert "`result_incomplete` alone is NOT a sufficient completeness check" in contracts
    assert "primary_truncated" in contracts, (
        "the contract no longer names the field a caller must branch on, which is the entire "
        "remedy for the 1-of-4-callers report"
    )
    assert "primary_omitted" in contracts


def test_contracts_documents_that_the_two_scan_count_fields_can_disagree() -> None:
    """Task 328/333: `scan_limit.scanned_files` vs `deadline_limit.files_scanned`.

    Near-identical names, different phases, and one payload can legitimately carry both with
    wildly different values (`247` and `0`) -- the map was already built and the scan that
    consumes it never started. Nothing in the payload explained which was authoritative, so a
    reader could not tell the difference from a bug. Documented rather than renamed: both
    fields are published and `docs/SUPPORT_MATRIX.md` binds a published field to >=90 days AND
    >=2 minor versions of DEPRECATED marking, so a rename is a dual-emit exercise that doubles
    the surface this campaign exists to shrink.

    Pinned against the SOURCE: collapse the two producers into one field and the premise fires.
    """
    repo_map = REPO_MAP_PY.read_text(encoding="utf-8")
    contracts = CONTRACTS_PATH.read_text(encoding="utf-8")

    # PREMISE -- the two counts are still written by SEPARATE blocks with separate meanings.
    # `build_repo_map` stamps the collection-stage count into `scan_limit` and the
    # deadline-stage count into `deadline_limit`; the doc's "can legitimately disagree" claim
    # only holds while both exist independently.
    assert '"scanned_files": capped_file_count,' in repo_map
    assert '"files_scanned": files_scanned,' in repo_map, (
        "the deadline-stage file count is gone or renamed; the CONTRACTS paragraph explaining "
        "why the two counts disagree may now describe a payload shape that no longer exists"
    )

    # THE CLAIM -- the doc must warn they differ AND name both fields explicitly, so a reader
    # hitting the confusing payload can search either name and land on the explanation.
    assert "Two similarly-named count fields mean different things" in contracts
    assert "scan_limit.scanned_files" in contracts
    assert "deadline_limit.files_scanned" in contracts
    # ...and must NOT advise comparing them, which is the wrong inference the names invite.
    assert "never a comparison between these two numbers" in contracts
