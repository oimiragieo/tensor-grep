---
name: tensor-grep-hermetic-hostile-tests
description: >-
  Use when writing a gated test that must pass identically on the dev desktop AND the CI pytest env
  (which lacks optional engines), constructing a HOSTILE fixture (permission denial, junction/symlink
  swap, network partition, disk-full, killed process, corrupted file, missing binary) and proving it
  actually BITES before the probe runs, forcing a deterministic injectable seam instead of
  env-detecting, mutation-asserting that a red-arm control really applied, or adding a positive
  control so a zero cannot read as "clean". Triggers: "hermetic test", "hostile fixture",
  "fixture-BITES precheck", "STILL VACUOUS", "env-independent", "setup-not-assertion", "the fixture
  never applied", "mutation asserted-applied", "positive control", "Event-gated parent swap",
  "RED reason class", "platform arms". Sibling of
  tensor-grep-codex-gated-audit-loop (the loop) and tensor-grep-validation-and-qa (what counts as
  proof); this one is the CONSTRUCTION discipline for hermetic + hostile tests. Not for deciding what
  to test or for merge gates.
---

# tensor-grep: hermetic + hostile test construction

The 2026-08-08/09 wave (M1, M3, M14, #979, #984) hard-confirmed two construction laws that keep
costing rounds when skipped:

1. **A hostile fixture is a claim about the world, and claims get verified.** A test whose
   permission-denial / junction / symlink-swap setup silently no-ops does not test anything — it
   reports a clean PASS on a fixture that never applied (oracle Form 6, AGENTS.md #281).
2. **A gated test that must pass in BOTH the dev env AND CI pytest envs must be env-independent BY
   CONSTRUCTION** (A85, #984): force a deterministic seam for the optional engine, never
   env-detect; a test that passes locally and fails CI on a missing engine is a DEFECT in the test,
   not the product.

The two laws are one discipline: a fixture (or a test environment) is *evidence* only once you have
independently proved it is the hostile thing you claim it to be.

---

## Part 1 — Hermetic construction (env-independent BY CONSTRUCTION)

### 1.1 Force a controlled deterministic seam — never env-detect

CI's pytest env lacks SOME of the optional engines the dev desktop has — but not all of them; know
which is which before forcing a seam. A gated test that reaches a tool's success arm through a real
engine flips its verdict between the two environments:

| Optional engine | Dev desktop | CI pytest env (`test-python`) | Flip |
|---|---|---|---|
| ast-grep binary | present | absent (installed only in the `agent-readiness` job, not `test-python`) | AST-wrapper tool success arms → absent-dep raise / "unavailable" envelope |
| tree-sitter native grammars | present | **PRESENT** — `test-python` installs `-e ".[dev,ast]"` and the `ast` extra carries every grammar | none — do NOT force a seam for these |
| dense model (model2vec) | present/corrupt | absent (`semantic` extra not installed) | `tg_find` dense → BM25 fallback |
| compiled `rust_core` Python extension | built | **PRESENT** — the maturin build backend compiles the PyO3 cdylib as part of the editable install | none — extension-backed probes run |
| STANDALONE native `tg` binary (`rust_core/target/release/tg`) | built | may be absent — `test-python` never deliberately builds it (maturin builds only the extension cdylib); claim IN DISPUTE, see the non-gating `Task 22 diagnostic` step in `ci.yml` | delegation-route probes → Python route |

(Verified 2026-08-12 against `ci.yml` `test-python` install steps + `pyproject.toml` extras/build
backend; grep `uv pip install -e ".[dev,ast]"` in `.github/workflows/ci.yml` and `ast =` /
`build-backend` in `pyproject.toml` to re-derive.)

**The mechanism (M14 census shape, `_AST_ENGINE_SHIM_FAMILIES` in `mcp_server`'s M14 test):** force
the engine's absence path through a controlled shim whose `__name__` is load-bearing (the tool
refuses a backend not named `AstBackend`), returning deterministic empty results — so the tool's
REAL success return site (e.g. `_inject_mcp_contract_fields`, `src/tensor_grep/cli/mcp_server.py`)
is value-checked identically everywhere. For the dense leg, the ratchet does NOT raise
`DenseUnavailableError`: `_force_dense_unavailable` in
`tests/unit/test_mcp_contract_stamp_ratchet.py` REPLACES `retrieval_dense.dense_available` with a
deterministic `(False, reason)` tuple, so every venue exercises the same BM25-only fallback success
path (the same answer the degrade writes into `rank_fallback_reason`).

Checked list for a hermetic gated test:

- [ ] The optional-engine dependency is FORCED through a seam, never detected from the env.
- [ ] A separate "simulated hostile env" test re-runs the census under a forced no-engine /
      corrupt-dense setting and asserts the verdict is UNCHANGED (proves the forcing worked).
- [ ] Error arms stay REAL: engine-free refusals (e.g. out-of-root confinement) run real code and
      stay value-checked on both envs.
- [ ] Any allowlist entry names the EXACT typed exception types the absent-dep path raises — it
      excuses ONLY those types, never "any error" (a bare allowlist masks the regression it exists
      to catch).

### 1.2 Mutation-control: a ratchet that cannot RED is decoration

Before trusting any census/ratchet-style gated test, prove it goes RED on each mutation (Form 1
applied to guards, AGENTS.md), and re-point the ratchet until it does:

- [ ] **Deleting a member** from the census/set/population → RED.
- [ ] **Removing a stamped path** (a wrapped caller, an envelope field) from a covered member → RED.
- [ ] **An allowlisted family raising an unexpected exception type** → RED.

The M14 sequence was the receipt: the ratchet (not the fix) took two extra codex rounds, and each
round found a harness defect (exception-allowlist masking, env-dependence on the dense model,
partial-key parity).

### 1.3 Platform arms: target AND non-target (A84)

A gated test that exercises a platform-meaningful path shape (a Windows junction, a drive-absolute
strip, a `/mnt/c` bridge) must pin BOTH arms: the TARGET platform where the shape is meaningful AND
the NON-target platform where the transform must be inert (A84). An unconditional platform-shaped
transform re-creates the escape on the sibling OS — the drive-absolute strip receipt: stripping the
leading `/` from `/C:/…` unconditionally turns a root-anchored URI into a RELATIVE path on POSIX,
flipping a confinement check from refused→passed. Gate the transform on `os.name == "nt"` (or its
POSIX analogue) and pin both arms in a cross-platform test; the junction fixture of Part 2 is the
same shape (the Windows junction arm plus a sibling symlink arm for the POSIX topology, neither
covering the other — the A27/A39 twin rule). The real CI OS matrix is the only oracle that catches
a platform flip; a single-platform local green proves nothing about the sibling arm.

---

## Part 2 — Hostile-fixture construction (BITE it, or it bites you)

### 2.1 The fixture-BITES precondition (Form 6, AGENTS.md #281)

A hostile fixture — permission denied, network partition, disk full, killed process, corrupted file,
missing binary, junction/symlink swap — **is a claim about the world**. Before the probe runs,
assert the fixture actually delivered the hostility, and abort loudly if it did not:

```python
try:
    os.listdir(denied_dir)
    raise AssertionError("fixture is vacuous: entries listed -> the deny did not apply")
except PermissionError:
    pass  # the fixture BITES
```

The `icacls`-fails-twice receipt (#281) is why this is non-negotiable: a deny ACE that does not
apply leaves the directory perfectly readable, tg returns a complete result, and the honest-looking
conclusion is *"no defect — the payload is intact."* The bug is declared absent by a test that never
tested anything. **A fixture is a claim about the world, and claims get verified.**

### 2.2 The repo's canonical hostile-fixture: Windows junctions on the M1 checkpoint guard

`tests/unit/test_checkpoint_create_ancestor_confinement.py` is the in-repo pattern for BOTH halves
of this discipline (M1, #982). Windows specifics that matter — a junction is NOT a symlink: `Path.is_symlink()` is False on a junction. **SUPERSEDED for the pinned Rust 1.96.0 toolchain: a real `mklink /J` junction reports `is_symlink: true` / `is_symlink_dir: true` / `is_symlink_file: false` (bounded probe receipt: docs/design/2026-08-13-replace-in-place-symlink-threat-model.md section 5); the CPython `os.path.islink()` half of the claim stays true.**

- Junctions (`mklink /J` on Windows, or `New-Item -ItemType Junction`) need **NO privilege**, unlike
  symlinks — so Windows is the attack platform you MUST exercise, not skip.
- `Path.is_symlink()` is **False** on a junction; `os.walk`/`os.scandir` descend junctions as plain
  directories. **SUPERSEDED for the pinned Rust 1.96.0 toolchain: a real `mklink /J` junction reports `is_symlink: true` / `is_symlink_dir: true` / `is_symlink_file: false` (bounded probe receipt: docs/design/2026-08-13-replace-in-place-symlink-threat-model.md section 5); the CPython `os.path.islink()` half of the claim stays true.** So the enemy property is "an ANCESTOR directory that resolves out-of-root", not
  "a leaf link".
- `mklink /J` requires the **LINK path to NOT pre-exist**; the **TARGET may be fully populated**.
  SUPERSEDED (2026-08-12 — was the A88 dogfood wording): this bullet previously said `mklink /J`
  "silently fails to create a junction when the target directory is NON-EMPTY". That claim is
  WRONG, verified 2026-08-12 two ways: (1) an empirical probe on this host — `mklink /J` against a
  target CONTAINING a file succeeds (exit 0, the junction resolves through to the file), while
  `mklink /J` onto a PRE-EXISTING link path fails (exit 1, "Cannot create a file when that file
  already exists", which reads as "silent" only when the checker captures output and looks at the
  exit code alone); (2) the repo's own fixture helper `_plant_ancestor_link_or_skip`
  (`tests/unit/test_checkpoint_create_ancestor_confinement.py`) removes the LINK path first
  (`shutil.rmtree(link)`) and then junctions to a target that CONTAINS `b.txt` — the fixture works
  precisely because a populated target is fine. The A88 dogfood fixture that "never applied" is
  therefore best explained by the link path still existing at creation time, not by a populated
  target (AGENTS.md's A88 entry still carries the old wording — out of scope for this skill, but
  do not re-cite it as authority for the non-empty-target claim). The BITE precheck stays
  mandatory regardless: assert the redirect actually resolves, AND assert the negative shape
  (`assert not link.is_symlink()` (for CPython pathlib) so a real symlink is not mistaken for a junction). **SUPERSEDED for the pinned Rust 1.96.0 toolchain: a real `mklink /J` junction reports `is_symlink: true` / `is_symlink_dir: true` / `is_symlink_file: false` (bounded probe receipt: docs/design/2026-08-13-replace-in-place-symlink-threat-model.md section 5); the CPython `os.path.islink()` half of the claim stays true.** See
  `_plant_ancestor_link_or_skip` plus the inline BITE precheck in each test body (grep
  `fixture is vacuous`) in that test module — there is no `_create_junction` / `_assert_fixture_bites`
  helper (was: cited here as `_create_junction` / `_assert_fixture_bites`; now: the real symbols).

Checked list for a junction/symlink hostile test:

- [ ] Create the redirect with the repo's platform helper (`_plant_ancestor_link_or_skip` —
      junction-first on Windows, symlink fallback, `pytest.skip` on `OSError`/`NotImplementedError`
      only when real link creation is genuinely impossible, per the standing Windows-symlink rule).
- [ ] **BITE precheck:** prove the redirect resolves into the out-of-root dir before the probe runs;
      abort with "fixture is vacuous" otherwise.
- [ ] **Negative shape pin:** `assert not link.is_symlink()` (for CPython pathlib) when the fixture is meant to be a
      junction — so the guard under test is the junction's parent-resolve containment, not a
      symlink refusal. **SUPERSEDED for the pinned Rust 1.96.0 toolchain: a real `mklink /J` junction reports `is_symlink: true` / `is_symlink_dir: true` / `is_symlink_file: false` (bounded probe receipt: docs/design/2026-08-13-replace-in-place-symlink-threat-model.md section 5); the CPython `os.path.islink()` half of the claim stays true.**
- [ ] Run the SAME probe against a plain (non-hostile) tree as the control arm — the hostile run
      must differ from the plain run.

### 2.3 Symlink-ancestor + junction-ancestor are DIFFERENT fixtures

M1's plan (`docs/plans/2026-08-08-backlog-completion-plan.md`, M1 section — grep `### M1`) and the
test module both treat the two separately: a leaf symlink is legitimately tracked and stored AS a
link, while a symlinked OR junctioned *ancestor* under root is refused because the OS traverses it
transparently and copies out-of-root content. Write a distinct fixture per topology; do not let one
passing arm "cover" the sibling (the A27/A39/twin rule) — and note the create-side guard
(`checkpoint_store.py` `_resolve_parent_within_root`, parent-chain-only resolve) and the undo-side
full-leaf resolve (`_resolve_within_root`) are separate contracts; see 2.4 for the seam receipt
(was: this paragraph cited the create-side guard as `_resolve_within_root at :149` — that is the
UNDO-side symbol with a bare line number; now: both symbols cited by name).

### 2.4 Event-gated parent-swap fixtures (A38/A48) — the TOCTOU a plant-once fixture cannot reach

Planting a link ONCE (2.2/2.3) proves a STATIC out-of-root resolve. It cannot prove the swap race:
an attacker swapping a PARENT directory or junction BETWEEN the containment check and the
create/lock/publication step (A38). The construction law for that stronger fixture:

- **Anchor to OPENED, identity-verified parent handles.** Resolve-then-act over path strings is
  the SHAPE under test, never the oracle — see `tensor-grep-cross-platform-path-confinement`
  Part 3 (Canonicalize-or-fail-closed vs opened-identity anchoring, A38/A48/A53).
- **Event-gate the swap** (never wall-clock overlap — a starved runner serializes legitimately and
  false-fails; assert the blocking CONTRACT with `threading.Event` handshakes, AGENTS.md A17/A27):
  swap the parent deterministically AFTER the guard's check/lock and BEFORE the step under test, in
  each of the three windows A48 names — before create, after lock, before publication/read.
- **Repo seam (verified 2026-08-12, cite by symbol):** the CREATE-side guard is
  `checkpoint_store.py::_resolve_parent_within_root` (parent-chain-only resolve; the leaf's raw
  identity survives — called from `create_checkpoint`'s copy loop), and the UNDO-side twin is
  `_resolve_within_root` (full-leaf resolve over the snapshot dir). Grep
  `def _resolve_parent_within_root` / `def _resolve_within_root` in
  `src/tensor_grep/cli/checkpoint_store.py` to re-derive.
- **Honest current state (A48 DEFERRED):** the atomic-publish helper `atomic_write_bytes_anchored`
  (`src/tensor_grep/cli/_index_lock.py`) is FSYNC-anchored (data fsync + directory fsync,
  `O_NOFOLLOW` temp creation, leaf-symlink refusal) but NOT identity-anchored — it does not yet
  open/verify parent handles. Until A48 lands, a full Event-gated parent-swap RED against the
  publish path stays **RED-by-design**: the TOCTOU is real and unclosed, so the RED is the correct
  expected state, not a fixture bug. Write the fixture, mark it RED-by-design with the A48 owner
  and reopen trigger, and do NOT relax the assertion to green.

---

## Part 3 — Mutation asserted-applied (the red-arm must actually RED)

**Behavioral-RED law (A61):** a RED arm must fail for the EXACT expected refusal/reason class. A
crash, import failure, hang, panic, or setup error is NOT a behavioral RED — it proves the arm
died BEFORE exercising the contract, and any arm that dies early invalidates the red phase
(AGENTS.md A61). Pin the expected exception type / message / exit code in the assertion
(`pytest.raises(ExpectedError, match=...)`, an exact exit code, an exact refusal marker), and
reject any arm whose failure output does not contain the pinned reason. "Any error is fine"
allowlists are the same mask here as in Part 1.1's typed-allowlist rule — there on the green arm,
here on the red one.

A red-arm attempt that silently no-ops is a control arm that never ran (validation-and-qa, "A
mutation that does not apply is a control arm that never ran"):

- [ ] After applying a red-arm mutation (reversion, stub, deletion), **assert it actually applied**:
      diff the file, or `grep` for the string you just removed / added — the "failing" run of an
      un-applied mutation is really the unchanged test passing.
- [ ] Capture the EXPECTED failure message in the assertion; its absence in output is the tell that
      the arm never ran.
- [ ] For a doc/ratchet perturbation, confirm your perturbation actually removed the property it
      guards — `grep -c` the string first (the `truncation_cause` receipt: one occurrence removed,
      test still green, because the property exists twice).

---

## Part 4 — Positive controls (a zero must be able to be non-zero)

Every probe carries a positive control — a zero means "measured nothing" or "never actually
checked", and the two are indistinguishable in the number (verification-oracle family, AGENTS.md):

- [ ] Before trusting a zero (no findings, no matches, no exceptions), show the SAME probe returns
      non-zero where it should: assert the census is non-empty AND prints each loaded module's
      `__file__`; assert the grep form matches a known-present instance (`ripgrep` → 162 hits).
- [ ] A rate at 0% or 100% is a property of the instrument far more often than of the subject —
      check what would have to be true of the world for it to be genuine, and probe that instead.
- [ ] The control fixture must itself be unambiguous: a known-good arm that cannot pass proves as
      little as one that cannot fail (the `pyproject.toml:1` basename-collision receipt).

---

## External anchors (Exa research, 2026-08-09)

The hermetic/hostile construction discipline is this project's instance of a well-known family of
"validating the validator" practices; the family validates the shape, ours is tuned to this repo's
evidence bar (real file:line-cited receipts, red-green controls, CI-parity gating).

| Anchor | Their point | This skill's mapping |
|---|---|---|
| **AIarch "Validating the Validator"** (aiarch.github.io) | The check that validates a check must itself be tested; a validator is only as good as the adversarial cases it demonstrably prevents — evaluate the gate, not just the feature. | Part 1.2's mutation-control (each mutation must RED) and Part 4's positive controls: proof that the gate can fail is the only proof it works. |
| **Atakua "Adaptive Deep Learning: Why We Should Care About 'How to Test the Tests'"** (atakua.com.br, 2018) | Test the tests: even an obviously-useful technique must prove the tests actually catch what they claim, because an un-failing test is indistinguishable from a test that tests nothing. | Part 1.2 (mutation REDs) and Part 2 (fixture-BITES): the test's own hostile setup is asserted to be hostile before it is trusted. |
| **Stack Overflow "How to test the test?" / testing-test-conf* threads** | Peer review of tests, coverage of the test's own claims, and proving the red-arm fires before green. | Part 3 (mutation asserted-applied): the red phase must be observed, not assumed. |

**Repo receipts to cite by symbol, not line (the never-re-stamp law):** AGENTS.md oracle **Form 6**
(grep `Form 6`), A85/A88 in AGENTS.md's A-law list; `tests/unit/test_checkpoint_create_ancestor_confinement.py`
(the junction BITE pattern); `tensor-grep-validation-and-qa` SKILL.md's "Writing a hostile fixture
(Form 6 defence)" and "A mutation that does not apply is a control arm that never ran" sections;
`docs/plans/2026-08-08-backlog-completion-plan.md` M1/M17 sections (the fixture-BITES precheck
requirements — grep `### M1` / `M17`).

---

## Quick reference

```
[1] env-independence   force the optional-engine seam (never env-detect) -> prove the force
[2] fixture-BITES      assert the hostile setup actually applied before the probe; abort if not
[3] junction != symlink  is_symlink()==False on a junction; mklink /J needs the LINK path absent (target may be populated) **SUPERSEDED for the pinned Rust 1.96.0 toolchain: a real `mklink /J` junction reports `is_symlink: true` / `is_symlink_dir: true` / `is_symlink_file: false` (bounded probe receipt: docs/design/2026-08-13-replace-in-place-symlink-threat-model.md section 5); the CPython `os.path.islink()` half of the claim stays true.**
[4] mutation REDs      delete a member / stamp / typed allowlist entry -> each must RED
[5] mutation applied   assert the red-arm mutation actually took (diff / grep) before trusting red
[6] positive control   the same probe must return non-zero somewhere it should
[7] RED reason class   a crash/import/hang/panic/setup error is NOT a RED; pin the exact refusal (A61)
[8] platform arms      pin the target AND the non-target platform arm; the CI OS matrix is the oracle (A84)
[9] parent-swap TOCTOU Event-gate swaps against opened identity-verified parents; the fsync-anchored publish stays RED-by-design until A48
```

The endpoint: a gated test that passes identically on the desktop and CI, whose hostile setup is
proven hostile, whose red arms are proven red — a test that cannot certify a lie.
