# Tensor-Grep v1.0.0 Release Plan (TDD 2026)

This plan outlines the final steps to bring `tensor-grep` to full 1.0.0 enterprise release parity with ripgrep, utilizing the 2026 standard **"Outside-In Double-Loop TDD"** process. 

## TDD Core Methodology (2026 Best Practices)

For every phase below, we will follow the rigorous **Red-Green-Refactor** cycle using the **Arrange-Act-Assert (AAA)** pattern. 

### 1. The Naming Convention
Every test must document behavior following the 2026 standard: `test_should_[expectedBehavior]_when_[scenario]`.
Example: `test_should_respectIgnoreCase_when_configFlagSet`

### 2. The AAA Pattern
Every test will explicitly be broken down into:
- **Arrange:** Set up the exact test data, mock the GPU environments, and configure the `SearchConfig`.
- **Act:** Execute the backend `search()` or `classify()` method.
- **Assert:** Verify the exact `SearchResult` matches expectations without over-asserting internal implementation details.

### 3. The TDD Workflow
1. Write a failing behavioral unit/integration test (**RED**).
2. Write the minimum logic in the backend/CLI to satisfy the test (**GREEN**).
3. Clean up the code, optimize the GPU calls, and ensure types are strictly enforced (**REFACTOR**).

---

## Phase 1: Wire CLI Flags into GPU Backends (cuDF & cyBERT)

While the CPU backend currently respects the `SearchConfig`, the high-performance GPU backends do not. We need to map ripgrep flags into native RAPIDS operations.

- [ ] **Step 1.1: cuDF String Operations (Case Insensitivity & Invert)**
  - **TDD:** Write `test_should_ignoreCase_when_usingCudfBackend` and `test_should_invertMatch_when_usingCudfBackend`.
  - **Implementation:** Modify `src/tensor_grep/backends/cudf_backend.py`. Map `config.ignore_case` to the `flags=re.IGNORECASE` equivalent in `cudf.Series.str.contains()`. Map `config.invert_match` to bitwise negation `~` on the resulting boolean mask.
- [ ] **Step 1.2: cyBERT Confidence Filtering**
  - **TDD:** Write `test_should_filterConfidence_when_nlpThresholdSet`.
  - **Implementation:** Modify `src/tensor_grep/backends/cybert_backend.py`. Ensure NLP classification logic drops predictions that don't meet thresholds or respects context line outputs if provided.

## Phase 2: Implement Advanced Ripgrep Formatting Features

Ripgrep is famous for its context awareness and advanced file filtering. We need to implement these inside our core pipelines.

- [ ] **Step 2.1: Context Lines (-A, -B, -C)**
  - **TDD:** Write `test_should_includeAfterContext_when_dashA_isProvided`.
  - **Implementation:** Update the `SearchConfig` mapping in `backends/cpu_backend.py` to capture `N` lines before/after a match, utilizing the `context_separator` (default `--`).
- [ ] **Step 2.2: Advanced File Filtering (-g, -t)**
  - **TDD:** Write `test_should_filterGlob_when_dashG_provided` and `test_should_filterType_when_dashT_provided`.
  - **Implementation:** Currently `tensor-grep` expects a single file path. We need to implement a directory traversal engine in `Pipeline` or a new `io/directory_scanner.py` that utilizes `config.glob` and `config.file_type` to yield matching file paths before handing them off to the IO readers.

## Phase 3: Multi-GPU Distribution and Scaling

Currently `tensor-grep` defaults to `cuda:0` (a single GPU). To truly leverage enterprise hardware, we need to distribute the parsing workload across multiple available GPUs.

- [ ] **Step 3.1: Hardware Discovery**
  - **TDD:** Write `test_should_detectMultipleGPUs_when_available`.
  - **Implementation:** Update `src/tensor_grep/gpu/device_detect.py` to return a list of available device IDs (e.g., `[0, 1]`) instead of just checking device 0.
- [ ] **Step 3.2: Workload Sharding (cuDF & cyBERT)**
  - **TDD:** Write `test_should_shardDataAcrossGPUs_when_multiGpuDetected`.
  - **Implementation:** Modify the `Pipeline` or `MemoryManager` to chunk incoming files not just by VRAM limits, but to distribute those chunks evenly across a multiprocessing pool or CUDA streams spanning multiple GPUs concurrently.

## Phase 4: Test and Trigger CI/CD Pipeline

With the software complete, we need to ensure the enterprise GitHub Actions build process works for the standalone Nuitka binaries.

- [ ] **Step 4.1: Final Integration Test Pass**
  - Run the full suite (`pytest tests/ -v`) to guarantee the advanced features didn't break baseline ripgrep parity. Ensure code coverage remains above 75%.
- [ ] **Step 4.2: Tag and Trigger**
  - Create a git tag `v1.0.0-rc1` (Release Candidate 1).
  - Push the tag to GitHub (`git push origin v1.0.0-rc1`) to trigger `.github/workflows/release.yml`.
- [ ] **Step 4.3: Verify Artifacts**
  - Monitor the GitHub Actions run to ensure the Windows, macOS, and Linux standalone binaries compile successfully via Nuitka.

## Phase 5: Native Package Managers (Homebrew & Winget)

To act like a true enterprise CLI tool, `tg` must be installable via native system package managers.

- [ ] **Step 4.1: Homebrew Formula**
  - Create a new repository or directory for the `Homebrew Tap`.
  - Write `tensor-grep.rb` outlining the curl download for the macOS binary and installing it to `/usr/local/bin/tg`.
- [ ] **Step 4.2: Winget Manifest**
  - Create the `oimiragieo.tensor-grep.yaml` manifest file mapping to the Windows `.exe` artifact generated in Phase 3.
  - Test the manifest locally using `winget validate`.
