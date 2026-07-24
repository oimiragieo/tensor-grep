---
name: tensor-grep-gpu
description: Use when exercising tensor-grep experimental GPU paths — devices/doctor probes, --gpu-device-ids search, agent GPU evidence, calibrate crossover. Distinguishes inventory detection from actual NativeGpuBackend promotion proof; CPU auto-fallback is not a GPU win.
---

# tensor-grep GPU (experimental)

Verified against **tg 1.95.0** (2026-07-24).

## Verdict, up front (do not bury this under the honesty table)

- **The shipped GPU kernel is a position-parallel brute-force byte-compare, NOT a PFAC/Aho-Corasick
  automaton** (`gpu_text_search_positions`, `docs/gpu_crossover.md:133-138` — PFAC remains documented
  future work, not what runs today).
- **No crossover at any measured scale**, including the best-case many-fixed-pattern lane: on a 1GB
  corpus, 100 fixed no-match patterns measured `rg -F -e ... -e ...` (the fair single-invocation
  baseline) at **0.169s** vs `tg`'s GPU-requested path at **0.448s** (which itself fell back to
  `NativeCpuBackend` in that measurement — not even a real GPU number). Historical worst case at 5GB is
  ~30-35x slower than `rg`.
- **Public CUDA-asset publishing is on a deliberate HOLD** (CEO decision package, task-store #169 — not
  a GitHub issue; re-verify with `gh issue list` before citing it as one). Release checksums currently
  ship **3 CPU-only rows**; there is no published nvidia asset to install.

## Preconditions

```bash
tg --version
tg devices
tg doctor --json REPO
```

## Enable CUDA-native front door (when published)

```bash
export TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR=nvidia
tg upgrade
tg doctor --json REPO
```

Many installs are **compiled without CUDA** — see `docs/gpu_crossover.md`.

## Commands

```bash
tg calibrate
tg search PATTERN REPO --gpu-device-ids 0 --json
tg agent REPO/src "task" --gpu-device-ids 0 --gpu-timeout-s 15 --json | jq .gpu_acceleration
```

## Honesty table

| Observation | Claim allowed? |
| --- | --- |
| `tg devices` lists GPUs | Inventory only |
| `routing_reason=gpu-auto-fallback-cpu` | **No** GPU win |
| `gpu_acceleration.promotion_claim=false` | **No** GPU evidence |
| Native GPU backend + `search_ready=true` | Conditional yes |

## Verified (2026-07-21, tg 1.91.0; WSL probe fix landed v1.93.0/#704)

Artifacts: `/tmp/tg-dogfood-v21/` (ephemeral scratch from that session — do not expect the path to
still exist; the findings below are the durable record).

- 2× GPU detected; calibrate FAIL; search GPU → CPU fallback; agent GPU evidence failed
- doctor `search_ready=false`, tier not usable
- **WSL probe (fixed v1.93.0, A11/#704):** the installer ships a bare-named POSIX shim `tg` wrapping
  `tg.exe`; cross-domain detection used to be `.exe`-suffix-only, so it misclassified the shim and
  produced an untranslated `/tmp` path → the reported `path_not_found`/`failed_probe_path`. Fixed by
  adding sibling-`tg-native-metadata.json` + co-located `<name>.exe` signals (fail-closed-only, capped
  metadata read). **After the fix, the same WSL bare-shim probe reports honestly:**
  `status=unsupported, routing_backend=NativeCpuBackend, routing_reason=gpu-auto-fallback-cpu, exit 0`
  — still no GPU acceleration (this build has no CUDA asset either way, per the verdict above), but no
  longer a misleading path-resolution error.
- **A14/#708:** `_agent_gpu_tg_command` now pre-resolves a bare `"tg"` via `shutil.which` before it
  reaches the cross-domain gate, closing a residual case where an unresolved bare command name skipped
  the WSL cross-domain check entirely.

## Related

- `tensor-grep`, `tensor-grep-enterprise-agent`, `tensor-grep-workspace-dogfood`
