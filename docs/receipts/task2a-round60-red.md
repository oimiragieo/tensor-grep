# Task 2A / #89 Round-60 RED receipt (Step 1 only) — candidate repair #9d

**Worktree:** `/home/james/.cursor/worktrees/tensor-grep/task2a-round60-red`
**Branch:** `task2a-round60-red`
**Git shape:** one unpushed RED commit on `origin/main`; derive the mutable candidate SHA externally.
**Plans (raw SHA-256 verified match):**
- design `31D8E071F1778A59888890445A0620000548AB270EFBE11F5F2E01A70E3D862B`
- implementation `AA64D0BA88BF98F07809065BD0E813B320C1CA7089804CDC1CD17FBB0B0826B3`

## Scope

RED phase only for the four Round-60 prerequisite groups. Behaviorless import
seams + injectable observable contracts; fail closed; no plausible success
receipts; no production installer/ledger/bridge/CI GREEN behavior; no
production-namespace test fakes. No Windows-project `uv`, no cold local cargo.
Candidate only — no Sol approval claim. Windows CI was **not** executed locally.

## Independent FIX-FIRST repairs applied (#9d on #9c)

Independent-review blockers only. Still no GREEN trust / Job / installer /
receipt / ledger product behavior.

1. **Authenticode fixture.** Replaced the invalid MZ-like blob with a copy of a
   real PE (`sys.executable`). `Set-AuthenticodeSignature` must return the exact
   signer certificate (thumbprint equality). Independent setup verifies exact
   signer thumbprint + `O=Microsoft Corporation`, root SHA-256 ∉ production
   allowlist, and cryptographic binding via a tampered copy requiring
   `HashMismatch`. Generic `UnknownError` with only a signer object is
   insufficient. Certs and files cleaned in `finally`.
2. **Untrusted catalog.** Exact `untrusted_catalog` arm now uses a held
   unsigned/foreign temp file with `WTD_CHOICE_CATALOG` (not trusted System32
   `wsl.exe`). Positive real `wsl.exe` catalog control stays in
   `test_held_file_embedded_and_catalog_controls`.
3. **Catalog member hash mismatch.** Binds held System32 `wsl.exe` with an
   independently wrong `expected_member_hash` derived from actual file bytes so
   production must hash the held handle and compare. Trusted-catalog positive
   ensures an always-mismatch implementation fails. No arbitrary non-catalog
   `.bin` + reason-only function.
4. **Job split + PID-bound heartbeat (#9d).** Platform-neutral injectable
   orchestration separated from mandatory Windows default-factory integration.
   Heartbeat is a pure function `descendant_job_pipe_heartbeat(pid)` (not a
   fixed parent-writable token). Real Windows node requires the descendant
   worker to emit that exact PID-bound payload before Job close; the received
   bytes must parse back to `fixture.descendant_pid`. Before `close_job_only`,
   independently prove BOTH retained process handles are `STILL_ACTIVE` via
   test-side `GetExitCodeProcess` and zero-time `WaitForSingleObject`. Then
   close Job only, prove both transition to exited / non-`STILL_ACTIVE`,
   boundedly drain buffered heartbeat data, and require EOF. Event clear is
   never proof. Vacuous pass if descendant already exited before close is
   refused.
5. **Job fault controls.** Injectable arms assert exact acquired-handle sets,
   exact termination sets, and exact reverse-acquire close order per
   `job_assignment` / `resume` / `image_query` / `pipe_worker_setup`. Mandatory
   Windows default-factory fault arms added (parametrized). Original
   `BaseException` preserved with cleanup notes.
6. **ProgramData ACL (#9d).** Independent inspector splits a pure SDDL DACL
   parser from the Windows SD→SDDL conversion helper. Extractor takes `D:` up
   to a top-level `S:` SACL (not `D:([^S]*)`, which truncates at `SY`). DACL
   flags parse only as exact `P`/`AR`/`AI` tokens and require `P`. ACE records
   must have exactly 6 fields; stray text outside flags/ACEs, unbalanced
   closes, unknown flags, numeric/unknown rights reject. `KA`=`KEY_ALL_ACCESS`
   and `KW`=`KEY_WRITE` are restricted write authority (never read-like);
   foreign KA/KW reject; SY+BA KA or KW can establish the exact required pair.
   Platform-neutral mutation vectors cover SACL boundary, KA/KW accept/reject,
   garbage flags/text, extra fields, unmatched close, permissive, missing-P,
   and malformed forms.
7. **CNG.** Removed unconditional manual `NotImplementedError`. Added
   behaviorless `windows_cng_primitives()` factory seam. Windows integration
   signs/verifies/reopens/verifies via production, checks stable key
   name/thumbprint, rejects tampered receipt, independently attempts NCrypt
   private export (exact non-exportability), always deletes the test key in
   cleanup. A future correct GREEN must pass. (Inspected #9c: no vacuous oracle.)
8. **TxR concurrency.** Removed vacuous `other_fired` assertion. Added
   Event-gated two-call injectable-adapter control
   (`test_txr_per_call_fault_isolation_event_gated`) proving fault adapter A
   cannot be invoked by call B. Injectable `txr=` orchestration wired (not
   GREEN Windows TxR). (Inspected #9c: no vacuous oracle.)
9. **Discover close.** Preserved exact close tests; added success-path cleanup
   failure arm (`test_discover_closes_protected_root_cleanup_failure_on_success`).
   No retained handle leaks (retry-safe close). (Inspected #9c: no vacuous oracle.)
10. **Ownership / manifest / receipt.** Full static concrete registry, AST
    markers, and manifest rows/digests recomputed. Receipt is candidate **#9d**
    proof-quality repair on **#9c**.

Prior #9/#8b CI census/ownership/observer work retained and extended: **148**
Python + **9** Rust = **157** manifest nodes; Counter collect equality.

## Independent RED runs (anti-hang, WSL-local `.venv`, `PYTHONPATH=src`)

Command shape (each suite):

```bash
timeout 120 env PYTHONPATH=src .venv/bin/python -m pytest \
  <suite> -q --timeout=15 --maxfail=0
```

| suite | result |
| --- | --- |
| `tests/unit/test_native_ci_receipt_v1.py` | **44 failed, 9 passed** |
| `tests/unit/test_installer_shim_receipt_v1.py` | **13 failed, 18 passed, 4 skipped** (SDDL vectors + KA/KW mutations pass) |
| `tests/unit/test_search_input_ledger_round60.py` | **32 failed, 3 passed** |
| `tests/unit/test_win32_path_domain_round60.py` | **2 failed, 11 passed, 12 skipped** (+heartbeat formatter in orchestration) |
| `tests/unit/test_task2a_ci_wiring_contract.py` | **6 passed** (YAML/governance; no network) |

Closed-world node counts: Python **148**, Rust **9**, manifest **157**.
CI node distribution: `test-python` Python **95**; `native-build-smoke` Python
**53** + Rust **9**.

Security fixture strategy: real-PE CurrentUser Authenticode generator for the
foreign same-Organization arm (no network); untrusted catalog on unsigned held
file; member-hash mismatch on held `wsl.exe` + wrong derived hash; Job arms use
injectable factory primitives + Windows default-factory integration with
pre-close PID-bound pipe heartbeat + pre-close STILL_ACTIVE proof +
`close_job_only` + bounded drain/EOF; ProgramData ACL via exact/conservative
pure SDDL DACL parser + Windows conversion helper; CNG via
`windows_cng_primitives` + test-side NCrypt export attempt.

`git diff --check`, `ruff check` (touched Python), `ruff format --preview
--check` (touched): clean. YAML parse of `ci.yml`: ok. No local cargo (A12).
Intended product tests stay RED; harness/governance / platform-neutral SDDL /
close / injectable TxR orchestration / heartbeat-formatter controls may pass.
CI wiring is present but was **not** executed locally. No Sol approval.

## Remains (reserved for later)

WSLENV shim corpus, pattern/ignore spelling ownership REDs, full bridge
Event-gated swap matrix GREEN, Rust ledger/`path_domain` crates, tagged A20
published-wheel/installer provenance dogfood, and any Windows-only Job Object /
CNG / WinVerifyTrust LIVE arms that cannot be closed under WSL. Plus GREEN
NativeCiReceipt emit/verify once CI census wiring is proven on Windows runners.
