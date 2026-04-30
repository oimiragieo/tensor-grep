# CHANGELOG


## v1.7.0 (2026-04-30)

### Documentation

- Refresh benchmark snapshot
  ([`068b898`](https://github.com/oimiragieo/tensor-grep/commit/068b8981efa4d4399aa038d2e1900e5c6d018d0c))

- Refresh v1.6.5 cold-path benchmark read
  ([`41d4e89`](https://github.com/oimiragieo/tensor-grep/commit/41d4e899711963296a63773b223f4adc97b2fd6f))

### Features

- Expose mcp runtime capabilities
  ([`6084638`](https://github.com/oimiragieo/tensor-grep/commit/6084638b77b44142b38c543486f90081a0c13c48))

Add MCP runtime capability discovery, structured native-unavailable errors, and updated harness
  documentation.

### Testing

- Add repo retrieval benchmark fixture
  ([`b8267b8`](https://github.com/oimiragieo/tensor-grep/commit/b8267b8933d62cbc8749e874d204b9c563713c1e))

- Cover word boundary cold-path attribution lane
  ([`7a7716a`](https://github.com/oimiragieo/tensor-grep/commit/7a7716a6ba6c0169736d491b36ef7390e125a6b5))


## v1.6.5 (2026-04-28)

### Bug Fixes

- Restore pypi ast rewrite path
  ([`bee9db3`](https://github.com/oimiragieo/tensor-grep/commit/bee9db321d90fa9ada26cfeea7bfea6d71aca4e8))


## v1.6.4 (2026-04-28)

### Documentation

- Add ast rewrite apply recovery plan
  ([`7a6521d`](https://github.com/oimiragieo/tensor-grep/commit/7a6521da89204964048325d7121972a89fe57e09))

- Refresh v1.6.3 benchmark snapshot
  ([`cabf21f`](https://github.com/oimiragieo/tensor-grep/commit/cabf21f2703b143814e564cfefb5e3dfdb44dfa2))

### Performance Improvements

- Recover ast rewrite apply fast path
  ([`5020602`](https://github.com/oimiragieo/tensor-grep/commit/5020602a051fdef60ce96ad1439d79b6853c9854))


## v1.6.3 (2026-04-27)

### Bug Fixes

- Disable rich help for redirected windows output
  ([`bfd2cc6`](https://github.com/oimiragieo/tensor-grep/commit/bfd2cc698993835a1e4f17d061c7912e77aea005))

### Chores

- Sync uv lock to v1.6.2
  ([`b85ea17`](https://github.com/oimiragieo/tensor-grep/commit/b85ea177df925a103f4e8d589896e8b968442fa2))


## v1.6.2 (2026-04-27)

### Bug Fixes

- Harden top-level pcre2 version probe
  ([`384f377`](https://github.com/oimiragieo/tensor-grep/commit/384f377d186e5e8299cf56862b904c72d52eb240))


## v1.6.1 (2026-04-27)

### Bug Fixes

- Align rg ast gpu front-door contracts
  ([`69a3178`](https://github.com/oimiragieo/tensor-grep/commit/69a3178f51967a0a4d9cd6930a7c0f1c2fcc4e9b))

- Format gpu benchmark skip test
  ([`bba3727`](https://github.com/oimiragieo/tensor-grep/commit/bba372769a858097aaf6ccdea565a4f2cb96fe51))

### Continuous Integration

- Install ast-grep benchmark comparator
  ([`c404296`](https://github.com/oimiragieo/tensor-grep/commit/c4042962b6de6c41df00b51afbfbe503ced28e26))

- Provision standalone ast benchmark prerequisites
  ([`b270674`](https://github.com/oimiragieo/tensor-grep/commit/b270674d3c29657d185eacc95fa40552804579db))


## v1.6.0 (2026-04-27)

### Bug Fixes

- Definitive PCRE2 support detection and skip logic
  ([`b41ea4c`](https://github.com/oimiragieo/tensor-grep/commit/b41ea4c865d5981a000067693975e62869169af2))

- Improved RipgrepBackend.supports_pcre2() to use --help and smoke test. - Refined skip logic in
  test_vs_ripgrep.py. - Final ruff format.

- Harden ci help and ast parity
  ([`6ada5cd`](https://github.com/oimiragieo/tensor-grep/commit/6ada5cdfd1a6222a61a61b226089035b39f250d2))

- Implement robust PCRE2 routing and CI test environment compatibility
  ([`600f31d`](https://github.com/oimiragieo/tensor-grep/commit/600f31d678b3a5876920b291507943ce3e99d99b))

- Added PCRE2 capability detection to RipgrepBackend. - Updated Pipeline to fallback to Rust core
  for PCRE2 if system rg lacks support. - Updated E2E tests to use sys.executable for reliable CI
  execution.

- Resolve PCRE2 routing and Pipeline initialization errors
  ([`0da7c70`](https://github.com/oimiragieo/tensor-grep/commit/0da7c7091da9395a9727eced69c1bf464c8bcdc3))

- Fixed NameError in Pipeline by reordering fallback_backend definition. - Ensured PCRE2 requests
  always route to RipgrepBackend. - Fixed RipgrepBackend to correctly pass -P and --max-filesize
  flags.

- Resolve python linting and PCRE2 lookahead regressions
  ([`55bda8f`](https://github.com/oimiragieo/tensor-grep/commit/55bda8f0ac2c488247683a957a721d5db29d53c2))

- Disabled ripgrep passthrough for PCRE2 patterns to ensure correct routing. - Fixed Ruff linting
  errors (unused variables and formatting). - Unified internal 'json_mode' naming in run_command.

- Resolve python linting error in ast_workflows.py
  ([`25e64fb`](https://github.com/oimiragieo/tensor-grep/commit/25e64fb891c4a3856e44d79b4242d550e5abdedc))

- Resolve python linting error in ast_workflows.py
  ([`8cedf81`](https://github.com/oimiragieo/tensor-grep/commit/8cedf8167a31b384f8dc6dc1cd6bf4346195dc4d))

- Resolve ripgrep backend pcre2 regression and CI test environment compatibility
  ([`8c8dda3`](https://github.com/oimiragieo/tensor-grep/commit/8c8dda363b970ad876ca6488d0ba9314da91557b))

- Added pcre2 and max_filesize support to RipgrepBackend. - Updated E2E tests to use sys.executable
  for reliable CI execution. - Archived implementation artifacts.

- Resolve rust main.rs compilation errors (duplicate fields and missing pcre2)
  ([`1ce1ec1`](https://github.com/oimiragieo/tensor-grep/commit/1ce1ec1e4a3409fc525dd3adee84c83eb0e45fba))

- Robust PCRE2 skip logic to handle ConfigurationError in CI
  ([`0ad47ab`](https://github.com/oimiragieo/tensor-grep/commit/0ad47abc876e71f2d5789d03facbf46df0f3dd03))

- Updated tests/e2e/test_vs_ripgrep.py to correctly catch routing errors when PCRE2 is missing. -
  Re-formatted to ensure CI linter parity.

- Skip PCRE2 tests when backend support is missing in CI environment
  ([`7845ba0`](https://github.com/oimiragieo/tensor-grep/commit/7845ba09e6feaf7d4ab233393e2ac3f40e00b037))

- Updated tests/e2e/test_vs_ripgrep.py with skip logic. - Final aggressive python reformatting to
  satisfy CI linter.

- Stabilize ci parity routing contracts
  ([`1c79f8d`](https://github.com/oimiragieo/tensor-grep/commit/1c79f8d73b6d0f1c4978d3db41de8698996f5b7e))

- Update RipgrepSearchArgs initializer in tg_search_fast.rs
  ([`d1810f4`](https://github.com/oimiragieo/tensor-grep/commit/d1810f4eeb232b7654187b444be3eaf3659ab6b0))

- Update RipgrepSearchArgs initializers in main.rs
  ([`2af1e68`](https://github.com/oimiragieo/tensor-grep/commit/2af1e68d6c102f17338140202e82afb21043c40d))

### Chores

- Re-trigger CI for final verification
  ([`b40aceb`](https://github.com/oimiragieo/tensor-grep/commit/b40aceb0d713898460b49ec730b8a129e5889f62))

- Trigger final CI stabilization
  ([`00b7ef1`](https://github.com/oimiragieo/tensor-grep/commit/00b7ef146c71b67393af2179d5128b63a3bab323))

### Code Style

- Definitive ruff reformatting with explicit line length for CI
  ([`42951fb`](https://github.com/oimiragieo/tensor-grep/commit/42951fbbcbc4d258b09241a28f0801ff8421fc10))

- Final aggressive python reformatting and linting fix
  ([`ff59c2a`](https://github.com/oimiragieo/tensor-grep/commit/ff59c2ac97c8c3678f2b0241b4bac031e182a451))

- Finalize python formatting and clean up implementaton scripts
  ([`b2dc2db`](https://github.com/oimiragieo/tensor-grep/commit/b2dc2db308c09508205d5de2eb07d74e6c4c4866))

- Finalize python formatting and re-trigger CI
  ([`1219608`](https://github.com/oimiragieo/tensor-grep/commit/1219608706fa429addb079a99d6214e0c9f676bd))

- Finalize python formatting and re-trigger CI
  ([`e818580`](https://github.com/oimiragieo/tensor-grep/commit/e8185801408f8396f3933067829f19e4afacd9be))

- Fix rust formatting in rg_passthrough.rs
  ([`1365c80`](https://github.com/oimiragieo/tensor-grep/commit/1365c80791846d4b3dcbdd9ef06cf57e1684f6ad))

- Normalize line endings and final ruff format
  ([`7d40cb2`](https://github.com/oimiragieo/tensor-grep/commit/7d40cb2194829c2def31031dcc8c7137ddfa19ec))

### Features

- Achieve 100% AST parity with ast-grep and harmonize CLI commands
  ([`ba52dd3`](https://github.com/oimiragieo/tensor-grep/commit/ba52dd36a04a361d573117491511eaf49d5194a2))

- Achieve 100% operational parity with ripgrep via PCRE2 bridge and operational limits
  ([`2464bbf`](https://github.com/oimiragieo/tensor-grep/commit/2464bbfa29d432fc8b91c6817ff7d05e7d36de39))

- Stabilize AST parity and CLI harmonization; update docs and release manifests
  ([`a97a584`](https://github.com/oimiragieo/tensor-grep/commit/a97a5840ce6dd6389ceb0c0188cd5f899d69d786))

- Stabilize AST structural search parity and CLI harmonization
  ([`7c06347`](https://github.com/oimiragieo/tensor-grep/commit/7c06347664befabe0599c63cdc59eafa41addba2))

- Unified 'run', 'scan', and 'test' command signatures in ast_workflows.py. - Implemented
  comprehensive JSON reporting for 'scan --json' including severity, fingerprints, and evidence
  snippets. - Integrated high-density 'llm' render profile for AST skeletonization. - Fixed backend
  caching logic to be class-aware, supporting robust monkeypatching in tests. - Verified
  implementation with 1,677 passing tests.

### Testing

- Update SearchRoutingConfig initializer in smart_routing tests
  ([`e169e5b`](https://github.com/oimiragieo/tensor-grep/commit/e169e5b3f490511c607ecd4688acc79a7b1cc2f3))


## v1.5.0 (2026-04-25)

### Bug Fixes

- Apply ruff format --preview to match CI requirements
  ([`f10871c`](https://github.com/oimiragieo/tensor-grep/commit/f10871c84dfd659e1a8bbfc7fe66b144611f6e0d))

- Resolve mypy type errors in python skeletonizer
  ([`f7d76e1`](https://github.com/oimiragieo/tensor-grep/commit/f7d76e1a3d2fd6ecbe25d77fd806bd7978e8e6c4))

- Restore docstring stripping for compact profile and sync rust_core version
  ([`7b72f7e`](https://github.com/oimiragieo/tensor-grep/commit/7b72f7e527d6e96188649f3f349e6caa345f9d0d))

### Chores

- Sync local formatting and refactoring from previous session
  ([`31399f7`](https://github.com/oimiragieo/tensor-grep/commit/31399f702e07c3afd5145af56d66b6b32331479f))

### Features

- Implement high-density llm render profile with python AST skeletonization
  ([`1703e02`](https://github.com/oimiragieo/tensor-grep/commit/1703e023920e528d3479529a77ffff4522e4e559))


## v1.4.12 (2026-04-25)

### Bug Fixes

- Bound context render full seed traversal
  ([`b3ac75d`](https://github.com/oimiragieo/tensor-grep/commit/b3ac75d09ff899bf59d51fbf152224316cf12450))

### Chores

- Sync lockfile for v1.4.11
  ([`906d4ba`](https://github.com/oimiragieo/tensor-grep/commit/906d4ba239bcc32339458b01ce3fd37d3b6f6ac9))


## v1.4.11 (2026-04-25)

### Bug Fixes

- Audit locked python dependency set
  ([`870337e`](https://github.com/oimiragieo/tensor-grep/commit/870337e7f031d8aac5ef350e809fa5814330277b))

### Chores

- Sync lockfile for v1.4.10
  ([`f698813`](https://github.com/oimiragieo/tensor-grep/commit/f698813d7dbfd3ded89bc965a161aff6495590bf))


## v1.4.10 (2026-04-25)

### Bug Fixes

- Route equals max-count through search frontdoor
  ([`432c206`](https://github.com/oimiragieo/tensor-grep/commit/432c206a67364c5e5ddd370f444e107081c30adc))

### Chores

- Sync lockfile for v1.4.9
  ([`22dac81`](https://github.com/oimiragieo/tensor-grep/commit/22dac81e3839e272079dd12f3a1f9fc3668d6126))


## v1.4.9 (2026-04-24)

### Bug Fixes

- Route positional word regexp searches
  ([`dbc93a3`](https://github.com/oimiragieo/tensor-grep/commit/dbc93a337bac4616f2edcc47103657e745e4591f))


## v1.4.8 (2026-04-24)

### Bug Fixes

- Apply ruff formatting to benchmark test
  ([`61a5cf4`](https://github.com/oimiragieo/tensor-grep/commit/61a5cf452e1dfd56e54039f6d48901f79b88c332))

- Format native benchmark env test
  ([`c2127d3`](https://github.com/oimiragieo/tensor-grep/commit/c2127d3616ffe849f520dbe970af817eaa1111ae))

### Performance Improvements

- Avoid duplicate native count search
  ([`f280d0c`](https://github.com/oimiragieo/tensor-grep/commit/f280d0c7af42e02e90e1a3b3043c9a1523f6fda2))


## v1.4.7 (2026-04-24)

### Bug Fixes

- Sync uv lock release version
  ([`8192a04`](https://github.com/oimiragieo/tensor-grep/commit/8192a04bd1d03cfb1902ddeb35d40e2950a5499f))


## v1.4.6 (2026-04-24)

### Bug Fixes

- Restore help and lock release parity
  ([`b22705d`](https://github.com/oimiragieo/tensor-grep/commit/b22705d0b9e894227a270cc81245bf05afb4bf82))


## v1.4.5 (2026-04-24)

### Bug Fixes

- Close merged rg parity contract gaps
  ([`84f5996`](https://github.com/oimiragieo/tensor-grep/commit/84f5996ce86b66c19ed4a8f3470fb21da7b03bfa))

- Format rg parity matrix tests
  ([`54a80b5`](https://github.com/oimiragieo/tensor-grep/commit/54a80b5004ed05938531bd4ad7f396714afc3738))

- Harden help fallback and native rg parity
  ([`51bd994`](https://github.com/oimiragieo/tensor-grep/commit/51bd994c4a2ea0b24f37a4cdca352cda2ccacbc0))

- Harden help parser for rich unicode output
  ([`5932d57`](https://github.com/oimiragieo/tensor-grep/commit/5932d57c58892b31c948deec635c50c0f989d86c))

- Harden tg help contract and rg parity surface
  ([`ab5a932`](https://github.com/oimiragieo/tensor-grep/commit/ab5a9324d74a1ab212a22a91206c3519098702a6))

- Skip ndjson parity without native tg
  ([`445a39c`](https://github.com/oimiragieo/tensor-grep/commit/445a39c68900e40292c6bbfcdc50c4930149a09d))

- Stabilize help parity checks in ci
  ([`f75236b`](https://github.com/oimiragieo/tensor-grep/commit/f75236b48ad4e4239ab8b1b23ed91eb8a17d43a2))

- Terminate stalled help passthrough trees on unix
  ([`aa50146`](https://github.com/oimiragieo/tensor-grep/commit/aa50146b74b2f7c18eb3cb560d8bacec7b726a64))


## v1.4.4 (2026-04-23)

### Bug Fixes

- Repair audit parity and front-door contracts
  ([`4783c27`](https://github.com/oimiragieo/tensor-grep/commit/4783c27f6854f0513d57a843ebe7274681b9db72))

- Repair audit parity and front-door contracts
  ([`8f65460`](https://github.com/oimiragieo/tensor-grep/commit/8f65460355deefdd716f217ee5bce2b233046e86))

- Restore dependabot repo context and ci format parity
  ([`f491213`](https://github.com/oimiragieo/tensor-grep/commit/f491213a221fe3387be1d74fffd43a74c83db95d))

- Restore native parity and ci stability
  ([`0fc3dbc`](https://github.com/oimiragieo/tensor-grep/commit/0fc3dbc22e65b631bc4f54eea0324788cb2cc415))


## v1.4.3 (2026-04-21)

### Bug Fixes

- Align native json routing and ast labels
  ([`0850753`](https://github.com/oimiragieo/tensor-grep/commit/085075362d974c2d1b1c8d72292c6ec2e3224699))

- Reject Python script shims as native tg binaries
  ([`e5c451e`](https://github.com/oimiragieo/tensor-grep/commit/e5c451e977fb98d0753aa4885fd76b6634baaa46))

- Repair release and runtime contract drift
  ([`cb28936`](https://github.com/oimiragieo/tensor-grep/commit/cb28936aa82c9b25aec525a03e8f38d510c48d4c))

- Repair search contract drift and reader reintegration
  ([`173276e`](https://github.com/oimiragieo/tensor-grep/commit/173276e31c68a026aa41535dd94c3e864b745976))

- Restore native replace bootstrap and ci format parity
  ([`149d70c`](https://github.com/oimiragieo/tensor-grep/commit/149d70c82396dfe4cec11799e1a5bed39fd275f0))

### Chores

- Normalize python formatting
  ([`5146f16`](https://github.com/oimiragieo/tensor-grep/commit/5146f16a464903a20d33725e587eafbf79720126))


## v1.4.2 (2026-04-19)

### Bug Fixes

- Harden search against binary reads and invalid input paths
  ([#24](https://github.com/oimiragieo/tensor-grep/pull/24),
  [`fbf0282`](https://github.com/oimiragieo/tensor-grep/commit/fbf0282a5d4c49545bb32b0f4002cf31895565aa))

* fix: harden search against binary reads and invalid input paths

* test: align CI formatter output for search path validation


## v1.4.1 (2026-04-19)

### Bug Fixes

- Strip ANSI styling from tg help assertion
  ([#23](https://github.com/oimiragieo/tensor-grep/pull/23),
  [`6b4bf6f`](https://github.com/oimiragieo/tensor-grep/commit/6b4bf6fdac1612be12b44486ed0b8643ec534775))

### Documentation

- Mention lexical repo-map retrieval in tg help
  ([#22](https://github.com/oimiragieo/tensor-grep/pull/22),
  [`e0b6e1c`](https://github.com/oimiragieo/tensor-grep/commit/e0b6e1c0003c00ad1c143a7ecf38a8d01943ea88))


## v1.4.0 (2026-04-19)

### Continuous Integration

- Repair security audit workflow and dependency floors
  ([#20](https://github.com/oimiragieo/tensor-grep/pull/20),
  [`541f2f8`](https://github.com/oimiragieo/tensor-grep/commit/541f2f81baad2f5ea4111bf613443d0bdc961117))

* ci: add explicit cargo-deny license policy for rust_core

* ci: fix pip-audit uv bootstrap in security audit workflow

* ci: format audit workflow validator changes

* ci: enforce python audit security floors

* ci: format preview formatter drift in audit branch

* ci: pin ruff for formatter parity

* ci: align tests with ruff 0.15.11 preview formatting

* test: skip hosted Windows throughput smoke floor

* test: fix hosted Windows throughput skip contract

### Features

- Improve repo-map lexical retrieval for planning and navigation
  ([#21](https://github.com/oimiragieo/tensor-grep/pull/21),
  [`d110dbf`](https://github.com/oimiragieo/tensor-grep/commit/d110dbf55fb3f5ffe5b91f83c1fc0b85f5bfdd8f))

* feat: improve repo-map lexical retrieval for planning and navigation

* test: align CI ruff formatter output

### Testing

- Add repository retrieval benchmark contract
  ([#18](https://github.com/oimiragieo/tensor-grep/pull/18),
  [`6a4fe05`](https://github.com/oimiragieo/tensor-grep/commit/6a4fe05dced351de38411e26845a8b65fc187e1d))

- Validate editable uv lock version parity
  ([#19](https://github.com/oimiragieo/tensor-grep/pull/19),
  [`b08430d`](https://github.com/oimiragieo/tensor-grep/commit/b08430d765d223eecfd4178e16809e6a7ef77b3e))

* test: validate editable uv lock version parity

* test: align replayed tests with ruff 0.15.11 preview formatting

* test: align replay formatter output with CI merge refs


## v1.3.2 (2026-04-19)

### Bug Fixes

- Honor ignore rules in files-without-match candidate collection
  ([#17](https://github.com/oimiragieo/tensor-grep/pull/17),
  [`5c25b75`](https://github.com/oimiragieo/tensor-grep/commit/5c25b75ba684bc418708fb5d7718a160478c81a7))

### Continuous Integration

- Fix preview formatter drift in benchmark validator follow-up
  ([#16](https://github.com/oimiragieo/tensor-grep/pull/16),
  [`ff691e4`](https://github.com/oimiragieo/tensor-grep/commit/ff691e4523ca8071992e5e9a0e8ccb5873135632))

- Split benchmark gate into base-compare and baseline-drift reporting
  ([#15](https://github.com/oimiragieo/tensor-grep/pull/15),
  [`eae326b`](https://github.com/oimiragieo/tensor-grep/commit/eae326b7f29fafbd43718cf8b602e445fa3f807d))


## v1.3.1 (2026-04-18)

### Bug Fixes

- Preserve recursive glob matching when case-folding
  ([`9d76ba4`](https://github.com/oimiragieo/tensor-grep/commit/9d76ba4d5a48c39e52be01553af2d254c494e05c))


## v1.3.0 (2026-04-18)

### Bug Fixes

- Honor case-insensitive glob filtering in directory scanner
  ([`8465172`](https://github.com/oimiragieo/tensor-grep/commit/846517208b530e179a7114e9c90b34f46e9e0739))

- **ast**: Resolve Rust function signature matching for typed parameters and return types
  ([`c79ef37`](https://github.com/oimiragieo/tensor-grep/commit/c79ef37e4a475bd9a64898da2ca15468fe5682d0))

- Targeted the remaining Rust AST miss where \n ()\ failed to match functions with typed parameters
  (e.g. \x: i32\) or return types (e.g. \-> i32\). - Fixed the matcher logic in \ackend_ast.rs\ by
  manually tracking the raw \$ARGS\ parameter and bypassing the rigid \Pattern::contextual\
  matching, which failed to parse incomplete parameters. - Added a failing regression test to
  \	est_ast_backend.rs\ to explicitly cover this behavior.

### Chores

- **test**: Silence dead_code warnings in test_schema_compat
  ([`00efcd1`](https://github.com/oimiragieo/tensor-grep/commit/00efcd19c3e4659d15dc6e31a07e7ecf7a86c5be))

- Added #![allow(dead_code)] to \	est_schema_compat.rs\ to eliminate noisy compiler warnings for
  schema structures used primarily for deserialization validation.

### Code Style

- Normalize preview formatting for release gates
  ([`136b831`](https://github.com/oimiragieo/tensor-grep/commit/136b831501b9719712e53360d6a83a527e246791))

- **rust**: Apply rustfmt after main merge
  ([`15763eb`](https://github.com/oimiragieo/tensor-grep/commit/15763eba6a0e3e0ffdc980ebaddf5397e7ad13c5))

### Documentation

- Add parity remediation execution plans
  ([`099dcc2`](https://github.com/oimiragieo/tensor-grep/commit/099dcc23b723401c77e101ce25376cf491b89133))

- Add parity remediation program spec
  ([`6a12026`](https://github.com/oimiragieo/tensor-grep/commit/6a1202615804e195ae435afbd73ec281716475c7))

- Add post-v1.3 safe release planning artifacts
  ([`d7ff5db`](https://github.com/oimiragieo/tensor-grep/commit/d7ff5db1f964d7984a39532b9f361d8df196cb57))

- Document count mode performance regression
  ([`8cb42ca`](https://github.com/oimiragieo/tensor-grep/commit/8cb42ca02bc069d10ffc0ccf07de4f776d45de4d))

- Explicitly recorded the regression observed on \-c\ (count matches) overhead vs \ipgrep\ in
  \docs/PAPER.md\, treating it as a regression to unwind in a dedicated optimization pass, separated
  from the AST correctness fix.

### Features

- Ship AST JSON parity and CLI contract fixes
  ([`0f06e3c`](https://github.com/oimiragieo/tensor-grep/commit/0f06e3ceb36cbb810c922df5aa14dd6697cf8154))

### Testing

- Bind subprocess imports to active worktree
  ([`954c073`](https://github.com/oimiragieo/tensor-grep/commit/954c0734f0acebf8398aa59ec4a0832edc152913))

- Clean up pytest warnings and tighten rust dead_code suppression
  ([`493fd04`](https://github.com/oimiragieo/tensor-grep/commit/493fd048312c41b5fc4668eb0039451568d400b8))

- Refresh windows benchmark baseline governance
  ([`5900eb6`](https://github.com/oimiragieo/tensor-grep/commit/5900eb603190d5bf5569404683351597601d7891))

- Stabilize completion CI and align ruff formatting
  ([`f2079e4`](https://github.com/oimiragieo/tensor-grep/commit/f2079e49a8a9a2006f531bbd706bc786dfe56e53))

- Stabilize inline-rules scan CI coverage
  ([`3a20a5d`](https://github.com/oimiragieo/tensor-grep/commit/3a20a5dc158e9ac1172c2582d6ff35d41b6b88b6))

- Tolerate rich formatting in generator errors
  ([`b0ea7ee`](https://github.com/oimiragieo/tensor-grep/commit/b0ea7eeda5fae75acbe6e5878e710f0a3aeeb9a3))


## v1.2.0 (2026-04-16)

### Bug Fixes

- Align runtime-path callsites and tests
  ([`9e84d7e`](https://github.com/oimiragieo/tensor-grep/commit/9e84d7e5f55b63cff7bad4e8b7cbe49481a9f06d))

- Align test formatting with CI ruff
  ([`1b01e08`](https://github.com/oimiragieo/tensor-grep/commit/1b01e085af4dd72bd9176963238da181c784ad69))

- Avoid python launcher recursion in native tg resolution
  ([`58e5bb9`](https://github.com/oimiragieo/tensor-grep/commit/58e5bb949a79a80d9083150713e4a2dbb639be31))

- Harden native tg resolution for launcher shims
  ([`9699d86`](https://github.com/oimiragieo/tensor-grep/commit/9699d864302a407b2661d7778359033b9c4796f8))

- Ignore benchmark tg binaries in runtime resolver
  ([`10bf30b`](https://github.com/oimiragieo/tensor-grep/commit/10bf30bc99670917b6385be4f3a78a4eb24d3b34))

- Normalize json golden output metadata
  ([`7797e04`](https://github.com/oimiragieo/tensor-grep/commit/7797e0480e93a2275b0694cba87a8a7c60c76528))

- Normalize python launcher snapshots across platforms
  ([`a894dad`](https://github.com/oimiragieo/tensor-grep/commit/a894dad79ad3d3089eb3de101b8f95560e3f3e2a))

- Preserve binary output parity without rg
  ([`d773d03`](https://github.com/oimiragieo/tensor-grep/commit/d773d03eeb179c9e4fb2ef41d10f7a99b4716864))

- Preserve per-file counts in rust fast path
  ([`c51c2b9`](https://github.com/oimiragieo/tensor-grep/commit/c51c2b959de01158ca50e41b74ddb879d943c52a))

- Resolve CI runtime path and native search regressions
  ([`273e23b`](https://github.com/oimiragieo/tensor-grep/commit/273e23bdda3124d3beb621284ef0e00950b8f9ef))

- Route bootstrap glob searches through full cli
  ([`590ded8`](https://github.com/oimiragieo/tensor-grep/commit/590ded87701469c199b99a77d0796cabb2fe8768))

- Skip native e2e cases without compiled binary
  ([`bfe0eaa`](https://github.com/oimiragieo/tensor-grep/commit/bfe0eaac0d27fa57f720f614e23aaf15e31ea095))

- Skip ndjson parity without native tg
  ([`a744315`](https://github.com/oimiragieo/tensor-grep/commit/a7443156167f8763beff02d4622e2eaf81587635))

- Stabilize golden output contracts across platforms
  ([`ad22df3`](https://github.com/oimiragieo/tensor-grep/commit/ad22df3ed6e380057a5d902f6bbd203c521f2247))

- Stabilize python launcher golden contracts
  ([`fe02711`](https://github.com/oimiragieo/tensor-grep/commit/fe027114f7867796ae56059e61b404c2f59971da))

- **core**: Fully resolve native positional -o formatting mismatch
  ([`6454e6f`](https://github.com/oimiragieo/tensor-grep/commit/6454e6fd8789970b41bdcf3b80ee0e0eabc3f45c))

- rust: removed hardcoded line_number: !cli.count fallback in positional_ripgrep_args and
  native_search_config_for_positional that incorrectly bypassed the explicit -n flag, finally
  allowing raw native searches like tg bar <file> -o to perfectly mirror ripgrep's formatting
  behavior - test: added explicit native regression tests
  (test_native_positional_search_only_matching_omits_line_numbers_by_default and
  test_native_positional_search_only_matching_includes_line_numbers_when_requested) to definitively
  lock in the positional -o formatting contract

- **core**: Perfectly align -n and -o output contracts with ripgrep terminal behaviors
  ([`c4be166`](https://github.com/oimiragieo/tensor-grep/commit/c4be166e089f3fb15d87547c4f23c2d9d129383b))

- cli: refactored Typer --line-number flag to accept None natively, enabling tensor-grep to fall
  back to isatty() detection identically to rg when not explicitly overridden - cli: fixed a
  regression in RipgrepFormatter where line numbers were unconditionally printed if the
  --line-number flag wasn't explicitly disabled, restoring proper -o bare token formatting on
  redirected streams and single files - rust: mapped the -n and --line-number flags through
  parse_early_ripgrep_args and the native PositionalCli so native fast-path text searches correctly
  append -n to the underlying rg execution when explicitly requested

- **core**: Restore full bootstrap test suite and fix -o line-number formatting
  ([`a002bff`](https://github.com/oimiragieo/tensor-grep/commit/a002bff88230adc7466fce6d528c4240d97e6702))

- test: restored the complete suite of test_cli_bootstrap.py behavioral tests that were accidentally
  overwritten, ensuring all passthrough and routing fallback behaviors remain actively tested
  alongside the new command registry parity checks - cli: fixed a regression in RipgrepFormatter
  where line numbers were unconditionally suppressed when only_matching was active. The formatter
  now correctly respects self.config.line_number, ensuring tg search --cpu -o outputs 1:bar natively
  in line with ripgrep's default contract

- **core**: Unify command registry across layers, solve -o format parity, and normalize invalid AST
  queries
  ([`b076ee2`](https://github.com/oimiragieo/tensor-grep/commit/b076ee239c35c115cb93f187839ecf2bfd5f2c02))

- cli: consolidated the hardcoded command lists from main.rs, main.py, and bootstrap.py into a
  single authoritative commands.py file, read at compile-time by Rust and at runtime by Python -
  cli: updated RipgrepFormatter and native args struct to perfectly align --cpu -o and default -o
  output formats with ripgrep's single-file matching contract - ast: normalized the error handling
  in AstBackend to catch parsing exceptions and emit an empty SearchResult envelope, matching
  AstGrepWrapperBackend's contract - ast: explicitly registered tree-sitter-rust inside AstBackend
  to fully support native S-expression queries against Rust codebases - tests: added an explicit
  structural integrity test (test_cli_bootstrap.py) verifying that commands.py, bootstrap.py, and
  the Typer CLI app dynamically align and fail-fast on drift

### Chores

- Commit benchmark artifacts for routing parity
  ([`27013dc`](https://github.com/oimiragieo/tensor-grep/commit/27013dcdb7925d897b0bc0946b6d65b8ac0a3669))

### Features

- Complete Phase 4, Phase 5, and Phase 6 hardening
  ([`355d3d0`](https://github.com/oimiragieo/tensor-grep/commit/355d3d06ca553d79a185f13399c7c04039969f81))

- Task 7 (Packaging and Runtime Path Hardening): Centralized runtime binary resolution in
  \	ensor_grep.cli.runtime_paths\. Passed \TG_SIDECAR_PYTHON\ from MCP server to ensure proper
  fallback execution context. - Task 8 (Benchmark Governance Hardening): Added explicit regression
  policies to \docs/benchmarks.md\ and \docs/PAPER.md\. Created \	est_benchmark_governance.py\ unit
  test to validate benchmark coverage rules. - Task 9 (Docs and Contract Surfacing): Explicitly
  documented routing parity scope, golden-output scope, launcher behaviors, and non-contract fields
  in \README.md\ under \## Product Contracts\. - Addressed ruff lint errors and ran all gates
  successfully.

### Testing

- Enforce routing parity matrix and output golden contracts
  ([`35213d2`](https://github.com/oimiragieo/tensor-grep/commit/35213d29ef7b72703af1edd59a1531f368d61ac3))

This commits the test suites for Task 5 and Task 6 with strict, deterministic execution.

Task 5 (Routing Parity): - Validates the routing backend and flag behavior across python-m,
  bootstrap, and native launchers. - Enforces character-for-character stdout/stderr parity for
  search operations. - Intentionally relaxes --help parity checks, as Clap (Rust) and Typer (Python)
  have inherently different help layouts and word-wrapping behaviors.

Task 6 (Output Golden Contract): - Snapshots un-sorted, raw grouping, and deterministic file outputs
  from the engine. - Uses '-j 1' to disable multi-threading non-determinism at the engine level. -
  Normalizes only the dynamic temporary directory path (<TMP_DIR>) to ensure snapshots are stable
  across CI environments without compromising the format contract. - Fixes RipgrepBackend
  thread-count propagation to ensure deterministic Python-fallback behavior.

- Relax bootstrap glob parity expectation
  ([`2983a94`](https://github.com/oimiragieo/tensor-grep/commit/2983a949fe274a83d2addd4f65537f09a775ffde))

- Stabilize glob parity across launchers
  ([`b688374`](https://github.com/oimiragieo/tensor-grep/commit/b6883748b4a0d8d212715e016ef13db3234073f0))

- **routing**: Add comprehensive routing parity matrix for CLI entrypoints
  ([`50d5def`](https://github.com/oimiragieo/tensor-grep/commit/50d5defe04bb4642f515f26f4ce0d929d456e8ea))

- Added an e2e table-driven test suite to exhaustively verify that all three primary execution modes
  (Python bootstrap, module execution, and native Rust binary) consistently route commands
  identically. - Includes extensive coverage of core subcommands (search, run, map, doctor, session,
  defs) and flags (-o, -r, --cpu, --json, etc.), ensuring no launcher diverges structurally or
  behaviorally.


## v1.1.4 (2026-04-14)

### Bug Fixes

- **ci**: Stabilize rust passthrough tests
  ([`b0c023d`](https://github.com/oimiragieo/tensor-grep/commit/b0c023d6b8c10179674bd88cb60f46263298da7d))

- **core**: Align AST workflow hints with native-first routing
  ([`312d7d6`](https://github.com/oimiragieo/tensor-grep/commit/312d7d6b33e81aa4591cbeb33525ef7cda2781ee))

- **core**: Fully restore AST native-first routing policy and prevent unsupported pattern crashes
  ([`511b659`](https://github.com/oimiragieo/tensor-grep/commit/511b659f9989f250d9373d7b67baaaaa6ef0f263))

- cli: changed the hard-coded st_prefer_native=False default to True in main.py and
  st_workflows.py to ensure 	g run can actually hit the native AstBackend when appropriate - cli:
  restored the pattern_kind == 'native' safeguard in _select_ast_backend_for_pattern to prevent
  non-S-expression queries (like def ()) from crashing the native 	ree-sitter parser, ensuring they
  correctly fall back to AstGrepWrapperBackend - tests: updated 	est_cli_modes.py to explicitly
  assert the new native-first default AST policy

- **core**: Make python passthrough work from checkout builds
  ([`ded17d6`](https://github.com/oimiragieo/tensor-grep/commit/ded17d625ec429f92e778c75f0bf51aba936678b))

- **core**: Remove debug output from native fast-path parsing that caused benchmark parity and
  timing regressions
  ([`686b8de`](https://github.com/oimiragieo/tensor-grep/commit/686b8de3726495acafbc1622e1c01befb19a161f))

- rust: stripped a rogue println! debug statement from try_default_search_frontdoor_passthrough that
  was erroneously writing to stdout during native Ripgrep passthrough operations - perf: resolved
  the massive benchmark regressions in the -C, -m, and -F test suites by eliminating the stdout
  buffering and benchmark suite parsing overhead caused by the debug output

- **core**: Resolve AST backend pattern routing, MCP native binary lookup, and CPU formatter parity
  ([`b954924`](https://github.com/oimiragieo/tensor-grep/commit/b954924d2d102cddf7e25ed9b4fea1af691457a9))

- cli: reverted the AST backend default optimization in _select_ast_backend_for_pattern to correctly
  prefer AstGrepWrapperBackend for standard ast-grep syntax patterns (e.g. def $F()), restoring
  execution parity - cli: updated RipgrepFormatter to respect Ripgrep's native single-file
  formatting contract, allowing --cpu and GPU modes to accurately omit the filename prefix when
  searching a single file - mcp: expanded _resolve_native_tg_binary to interrogate shutil.which for
  global/pip tensor-grep installations, fixing the FileNotFoundError that crashed AST rewrite plans
  in non-developer environments

- **core**: Restore native build integrity and align editor-plane clap parsing
  ([`edda28e`](https://github.com/oimiragieo/tensor-grep/commit/edda28e99fce2b45ff16475e318da9dee7653f3d))

- rust: removed obsolete Defs, Refs, and Context structured match blocks from un_command_cli to
  align with the new unified Vec<String> python passthrough models, eliminating the E0599 missing
  variant compile errors - rust: added disable_help_flag = true to all editor-plane commands in the
  Commands enum, guaranteeing clap safely delegates --help arguments directly to the Python Typer
  application without prematurely halting execution - python: restored AstBackend as the default
  optimization fallback in st_workflows.py, ensuring standard raw S-expressions correctly process
  natively through the PyO3 tree-sitter implementation

### Build System

- Sync uv lock with 1.1.3 metadata
  ([`d839754`](https://github.com/oimiragieo/tensor-grep/commit/d839754d10b0bf74946915ca0fed9ecedce3bbe1))

### Documentation

- Clarify native AST runtime dependency on environment availability
  ([`dd589a3`](https://github.com/oimiragieo/tensor-grep/commit/dd589a3b86bd8aac251545e7de40b58f2ca7fcb5))

### Testing

- Accept forwarded editor-plane help from combined output
  ([`5b28b0d`](https://github.com/oimiragieo/tensor-grep/commit/5b28b0d46688aad7d11c5776e9f19209f5abcd3d))


## v1.1.3 (2026-04-13)

### Bug Fixes

- **core**: Add all remaining Typer commands to python bootstrap known commands list
  ([`c64c890`](https://github.com/oimiragieo/tensor-grep/commit/c64c8909b0da7fdd2aa990638e4a293a4f5760e0))

- cli: ensure context-render, edit-plan, last-radius, last-radius-render, last-radius-plan,
  ulesets, udit-verify, udit-history, udit-diff, eview-bundle, and update are correctly
  acknowledged in ootstrap.py and main.py's known command sets, preventing any remaining top-level
  subcommands from improperly routing to the ripgrep fallback path

- **core**: Perfectly align native subcommand routing and output formatting
  ([`b77c21f`](https://github.com/oimiragieo/tensor-grep/commit/b77c21f447d712bd4aceacebeb9fe899a804612f))

- cli: ensure all commands (map, doctor, session, checkpoint) execute their Typer definitions rather
  than mapping incorrectly into Ripgrep search patterns through the bootstrap layer - cli: unblock
  the native Ripgrep passthrough for -o and --only-matching by un-listing them from python-required
  search flags in bootstrap.py, allowing the rg native format (which natively suppresses the
  filename prefix for single files) to execute accurately

### Documentation

- Update README for CLI parity fixes
  ([`d9dc724`](https://github.com/oimiragieo/tensor-grep/commit/d9dc7245da69f301b2b219f1f168fd10dfbbdf6d))


## v1.1.2 (2026-04-13)

### Bug Fixes

- **ci**: Align linux ruff preview formatting
  ([`ac743e4`](https://github.com/oimiragieo/tensor-grep/commit/ac743e45b0c436d1ab266e22b6dcbc89e9266da7))

- **ci**: Align preview formatting and force-cpu routing tests
  ([`284f279`](https://github.com/oimiragieo/tensor-grep/commit/284f279b0321eb6d1e779ee12bdcced0a439cf28))

- **core**: Address CLI routing, ripgrep passthrough, and output bugs
  ([`c00fe31`](https://github.com/oimiragieo/tensor-grep/commit/c00fe31fe253fb71572cb891ec5e5bdb098786c0))

- cli: ensure all remaining subcommands (doctor, map, session, checkpoint, etc) are correctly routed
  to the python passthrough layer in native Rust binary - cli: disable early ripgrep passthrough if
  --replace or -r is provided, preventing ignored replace operations - cli: add --color and -o /
  --only-matching argument mapping to PositionalCli and early ripgrep parser, enabling correct
  output formatting and preventing Clap parser crashes - cli: allow forced --cpu searches to
  natively route to ripgrep if structured output is not needed, bypassing the 6x slower custom Rust
  CPU backend - cli: fix --json flag being ignored in tg run when --apply is false

- **core**: Apply replacement string to output matches in Python CLI
  ([`32e0ee3`](https://github.com/oimiragieo/tensor-grep/commit/32e0ee3421acbf564a82ebdf9180c194f5663d59))

- **core**: Resolve native build failures and ensure search replace routes to python
  ([`06ddc06`](https://github.com/oimiragieo/tensor-grep/commit/06ddc060f7d5b611f0c61bb9a2743d9cce827fa8))

- rust: add missing color and only_matching fields to early RipgrepSearchArgs initializers, fixing
  the build breakage in tg_search_fast.rs and main.rs - rust: add replace field to SearchArgs and
  properly route tg search -r requests natively through to the python passthrough logic - rust:
  cleanly map color and only_matching down into the underlying ripgrep invocation process builder -
  rust: removed obsolete routing fallback test
  test_routing_cpu_failure_falls_back_to_ripgrep_passthrough since native cpu is fully bypassed when
  ripgrep handles --cpu requests - chore: removed stray test.rs file from the repository root

- **enterprise**: Ensure missing python subcommands route to the correct native executor and update
  help
  ([`19f965b`](https://github.com/oimiragieo/tensor-grep/commit/19f965bf38269eb5dac920a6b9bcfc3530d693a1))

- cli: added missing editor and testing subcommands to the native positional executor whitelist to
  ensure they are handled properly by their explicit sub-commands, resolving the bug where they
  incorrectly fell back to native search modes - cli: explicitly disabled the implicit ripgrep
  passthrough optimization when --replace (-r) is present to guarantee Python-layer string
  replacement logic correctly executes

### Chores

- Format codebase and align benchmark comparison docs
  ([`521b8f9`](https://github.com/oimiragieo/tensor-grep/commit/521b8f98c6fd4bd7a9656163be82e389c03b4a70))

### Continuous Integration

- Align formatter-sensitive test files with ubuntu ruff
  ([`f9459f1`](https://github.com/oimiragieo/tensor-grep/commit/f9459f14fc830a922daa4bdb1d62edee7dfd7ab3))

- Align ubuntu ruff formatter output
  ([`9a68c24`](https://github.com/oimiragieo/tensor-grep/commit/9a68c2473ebd84c1d05eb5dc72f9948afe515e18))

- Apply ruff preview formatting
  ([`63c586b`](https://github.com/oimiragieo/tensor-grep/commit/63c586bf19e9c841fe578eb41b175769e8fa2fa3))

- Automate audit issue remediation and document pipeline
  ([`5cb1a6f`](https://github.com/oimiragieo/tensor-grep/commit/5cb1a6f73f2396658e6ed4dd32460f9050ef6e52))

- Automate dependency maintenance with dependabot
  ([`ac07baf`](https://github.com/oimiragieo/tensor-grep/commit/ac07baf65af0dec9ab2ddc6dd5696a8461559e80))


## v1.1.1 (2026-04-12)

### Bug Fixes

- **docs**: Correct strict docs site links
  ([`200c98e`](https://github.com/oimiragieo/tensor-grep/commit/200c98eaa7557b4d5614061b7c3d899ae85b2d81))

- **enterprise**: Correct doctor summary text and add missing worker command
  ([`20df7cb`](https://github.com/oimiragieo/tensor-grep/commit/20df7cb017f6bed2aa72cdaf46a3403b1e87f28d))

- cli: update top-level doctor help string to accurately reflect new GPU and Cache diagnostics -
  cli: add hidden worker command to python entrypoint, ensuring tg worker correctly delegates to the
  native binary and renders appropriate help text

### Documentation

- Catalogue experimental features and hidden commands
  ([`e822fbc`](https://github.com/oimiragieo/tensor-grep/commit/e822fbc5bb87ef5c67036bdfd2dca5c5b36bd68e))

- **enterprise**: Tighten public docs and governance checks
  ([`37ba64f`](https://github.com/oimiragieo/tensor-grep/commit/37ba64f2b5b584e2c794265773e121d8acc7fc82))


## v1.1.0 (2026-04-12)

### Bug Fixes

- **ci**: Apply ruff preview formatting for enterprise readiness
  ([`a2124b5`](https://github.com/oimiragieo/tensor-grep/commit/a2124b519d44d5db7913f762f1b2bd3bd1296edc))

### Features

- **enterprise**: Harden supply-chain security and observability
  ([#13](https://github.com/oimiragieo/tensor-grep/pull/13),
  [`4144b61`](https://github.com/oimiragieo/tensor-grep/commit/4144b6130346f49b9095820abc32b072e4a4ee0c))

* fix(enterprise): resolve doctor rendering, strict AST staleness, and Node 20 deprecation

- cli: ensure tg doctor text output renders all new GPU/Worker/Cache fields - cli: upgrade AST cache
  staleness check to evaluate sgconfig.yml and internal project_data_v6.json dependencies - ci:
  enforce SLSA/SBOM step contracts in release workflow validators - ci: bump all actions to latest
  major versions (checkout@v6, setup-python@v6, setup-node@v6) to resolve Node 20 deprecation
  warnings

* fix(enterprise): resolve doctor rendering, strict AST staleness, and CI action contracts

- cli: explicitly assert actual recorded cache dependencies' st_mtime_ns matching Rust invalidation
  rules - ci: mandate all newly-upgraded action majors (checkout@v6, setup-python@v6, etc) in ci
  workflow validator - ci: upgrade SBOM/SLSA/Sigstore release checks from raw presence tests to
  structural step-level contracts

* fix(enterprise): resolve remaining doctor alignment and action validation gaps

- cli: update \	g doctor\ to accept and resolve non-default \--config\ paths, perfectly aligning the
  cache staleness check with the Rust orchestrator's behavior - ci: mandate strict action-major
  versions per-job using structural regex validation in \alidate_ci_workflow_content\, preventing
  any individual job drift

* fix(enterprise): align doctor config parsing and clean up release validators

- cli: update tg doctor signature to officially expose the --config flag, ensuring non-default
  config paths correctly feed into the AST staleness diagnostics - ci: remove trailing whitespace in
  validate_release_assets.py to restore strict ruff linting compliance

* fix(ci): pin setup-uv to published v8.0.0 tag


## v1.0.1 (2026-04-12)

### Bug Fixes

- Align tests with CI ruff formatter
  ([`3b4ea31`](https://github.com/oimiragieo/tensor-grep/commit/3b4ea31f0161591a0c4f69d0be2cd4bb4e2b65fb))

- Apply rustfmt for release pipeline parity
  ([`2f5c52c`](https://github.com/oimiragieo/tensor-grep/commit/2f5c52cd043d51208a84346e95930d68cf6a2d23))

- Resolve clippy blockers in rust release path
  ([`0871e08`](https://github.com/oimiragieo/tensor-grep/commit/0871e088fbbed95f95e3ce09058ab0d8f50700c6))

- Resolve remaining CI blockers for release publish
  ([`d7ae2fa`](https://github.com/oimiragieo/tensor-grep/commit/d7ae2fa6d6d7752b0d060a9c99ac380fa6234834))

- Restore benchmark submodule metadata and repo hygiene
  ([`fd36e55`](https://github.com/oimiragieo/tensor-grep/commit/fd36e55e12d122b51ee04d32380d03a815188beb))

- Trigger production release pipeline after v1.0.0 cleanup
  ([`c50dfc8`](https://github.com/oimiragieo/tensor-grep/commit/c50dfc8b870f6179c3bdc2d292974621a680b602))


## v1.0.0 (2026-04-11)

### Bug Fixes

- Make Windows tg update defer self-replacement
  ([`6baaab7`](https://github.com/oimiragieo/tensor-grep/commit/6baaab788f2215f81467b7656e4998fb94828b7c))

- Routing failures for native editor-plane and Python passthrough
  ([`9c857d7`](https://github.com/oimiragieo/tensor-grep/commit/9c857d78ecee20c54fdf1c919a4bcf8c7f3c5f26))

### Documentation

- Record rejected max-count frontdoor widening
  ([`f24d626`](https://github.com/oimiragieo/tensor-grep/commit/f24d626aca522688563d1d7a27a9a7ae6f28c18e))

- Record rejected positional glob probe
  ([`e09a0de`](https://github.com/oimiragieo/tensor-grep/commit/e09a0decb5298e4d5f478ebd6f2af55fc0792be7))

### Features

- Add doctor diagnostics command
  ([`55f53e0`](https://github.com/oimiragieo/tensor-grep/commit/55f53e0525a3691e86949d7456fc8465f92d50aa))

- Native AST orchestration, resident worker, and editor plane
  ([`6cca612`](https://github.com/oimiragieo/tensor-grep/commit/6cca612814e71eb88a23cf216fbbc22ce2c161d0))

- Support positional max-count routing
  ([`612548d`](https://github.com/oimiragieo/tensor-grep/commit/612548deec928999c3c86807a96c79dd2bc470e8))


## v0.35.1 (2026-04-02)

### Bug Fixes

- Cut ripgrep passthrough startup overhead
  ([`7b53749`](https://github.com/oimiragieo/tensor-grep/commit/7b537497cfcfae9c7517a7b71600830890f64c83))


## v0.35.0 (2026-04-02)

### Bug Fixes

- Harden hot-query benchmark contract
  ([`0224ddc`](https://github.com/oimiragieo/tensor-grep/commit/0224ddc47ecc10046698ac3f690ea1c0e0b12ffd))

Make the repeated-query benchmark runnable and honest across local and CI flows.

- install stringzilla in the bench extra - require CI benchmark jobs to use .[bench,dev] and run the
  hot-query suite - measure the fixed-string row through a fresh subprocess probe - record SKIP with
  an install hint instead of crashing when benchmark extras are missing locally - update benchmark
  docs, paper, README, and changelog with the refreshed artifact and narrower next cold-path targets

### Features

- Improve ai handoff prefetch and cached postings
  ([`f7ebbfb`](https://github.com/oimiragieo/tensor-grep/commit/f7ebbfbe834410f990fa760b605f91eef2cecdda))


## v0.34.0 (2026-04-01)

### Continuous Integration

- Enforce PR release intent for semantic versioning
  ([#9](https://github.com/oimiragieo/tensor-grep/pull/9),
  [`c07633e`](https://github.com/oimiragieo/tensor-grep/commit/c07633efec09ba438c3477545b17665f969fe01e))

* ci: enforce PR release intent and honest updater messaging

* docs: add PR release-intent contract to agents guide

* fix: retry initial daemon session lookup

* fix: harden session daemon metadata races

### Features

- Surface AI workflows in top-level help ([#10](https://github.com/oimiragieo/tensor-grep/pull/10),
  [`a490fb5`](https://github.com/oimiragieo/tensor-grep/commit/a490fb5f7849aacc56242bf72ff2241064eab645))


## v0.33.0 (2026-04-01)

### Features

- Stabilize AI handoff and validation pipeline
  ([#8](https://github.com/oimiragieo/tensor-grep/pull/8),
  [`ff08db3`](https://github.com/oimiragieo/tensor-grep/commit/ff08db390f159d8bb3d96f7d82ab586a44c1015c))

* feat: add agent edit tooling contracts

Add repo map/context pack, selective rewrite controls, post-apply validation, checkpoints, and
  symbol navigation surfaces for defs/impact/refs/callers.

* feat: add reusable edit sessions

Add cached repo-map sessions with CLI and MCP entry points, validate the new session JSON contracts,
  and document the workflow surfaces.

Also normalize the Python formatting drift that Ruff was already flagging so the full local lint and
  test gates stay green.

* docs: describe repo-map coverage limits

Add an explicit coverage object to repo-map-derived outputs so agent consumers can tell these
  surfaces are python-first and heuristic rather than a full cross-language semantic index.

Sync the committed JSON examples, cookbook/API docs, MCP assertions, and Rust schema validators to
  the new contract.

* feat: broaden repo-map inventory coverage

Extend repo-map inventory and exact definition lookup beyond Python by extracting imports and
  top-level symbols from JavaScript, TypeScript, and Rust sources.

Keep refs/callers explicitly python-ast, update the coverage contract to reflect the broader
  inventory scope, and sync the docs/examples/schema validators to that boundary.

* feat: add heuristic cross-language refs and callers

Extend symbol refs and callers beyond Python by adding heuristic JavaScript, TypeScript, and Rust
  line-based matching for exact symbol names and call sites.

Update the coverage contract to make the tradeoff explicit: Python keeps AST-backed navigation while
  JS/TS/Rust now use heuristic matching, with examples/docs/schema validators synced to that
  boundary.

* feat: improve cross-language test association

Boost repo-map test ranking with import-aware heuristics so impact, context, and callers can surface
  TS and Rust tests even when filenames do not mirror source stems.

Update the coverage contract, examples, docs, and schema validators to reflect the stronger
  filename+import heuristic.

* feat: add long-lived session serve loop

Add a JSONL-based session serve command that reuses cached repo-map sessions for repeated repo_map,
  context, defs, impact, refs, and callers requests without rebuilding inventory each call.

Document the session-serve contract and validate it with CLI and harness doc tests so the long-lived
  surface stays aligned with the one-shot edit tooling outputs.

* feat: reject stale cached sessions

Detect on-disk changes for cached session files and return a stale_session error instead of serving
  outdated repo-map responses.

This keeps the new session serve loop honest until an automatic refresh path is added.

* feat: add session refresh recovery

Add explicit session refresh plus optional --refresh-on-stale handling for the long-lived session
  serve loop so cached edit sessions can recover after file changes.

Also exclude .tensor-grep cache state from repo-map inventory so session metadata never pollutes
  repo context.

* feat: add mcp session refresh parity

Expose tg_session_refresh over MCP so harness clients can recover cached sessions after file changes
  without dropping down to the CLI.

Update the public harness docs and validator-backed tests to keep the session refresh contract
  aligned across CLI and MCP surfaces.

* feat: add import-graph context ranking

Boost context and impact file ranking with import-derived dependency edges so importer files outrank
  filename-only noise when a query matches real symbol definitions.

This follows the repo-map graph direction used by current agent editing tools while keeping the
  payload contracts unchanged.

* feat: add exact symbol source retrieval

Expose repo-map-backed source extraction over the CLI and MCP so agents can fetch exact symbol
  bodies instead of only definition locations or ranked file guesses.

Keep the contract honest with Python AST extraction plus heuristic JS/TS/Rust block extraction, and
  sync the docs, examples, and schema validators to the new source.json shape.

* feat: rank related tests through import graph

Extend repo-map context and impact ranking to walk the reverse import graph so tests reached through
  intermediate modules can outrank filename-only matches.

Update the declared coverage contract to filename+import+graph-heuristic and sync the examples,
  docs, and schema-backed tests to that stronger heuristic layer.

* feat: use parser-backed javascript navigation

Replace the JavaScript refs/callers path with tree-sitter-backed traversal so string and comment
  noise stop appearing as executable call sites.

Keep TypeScript and Rust on the existing heuristic path for now, and update the public coverage
  contract to python-ast+parser-js+heuristic-ts-rust so agent consumers can reason about the
  remaining limits.

* feat: use parser-backed typescript navigation

Add tree-sitter-typescript to the declared AST tooling dependencies and route TypeScript
  refs/callers through parser-backed traversal instead of regex heuristics.

Update the public coverage contract to python-ast+parser-js-ts+heuristic-rust and sync the docs,
  examples, lockfile, and validator-backed tests to that new capability boundary.

* feat: use parser-backed rust navigation

Add tree-sitter-rust to the declared AST and dev dependency surfaces and route Rust refs/callers
  through parser-backed traversal instead of heuristic regex matching.

Update the public coverage contract to python-ast+parser-js-ts-rust and sync the docs, examples,
  lockfile, and validator-backed tests to the fully parser-backed non-Python navigation surface.

* feat: unify parser-backed symbol source inventory

Route JS, TypeScript, and Rust symbol inventory and source-block extraction through the same
  parser-backed layer used by refs and callers so defs/source no longer depend on comment-prone
  regex detection.

This keeps import extraction unchanged, but makes the non-Python symbol surfaces internally
  consistent and removes commented-out definition noise from source retrieval.

* feat: expose context ranking provenance

Add file and test match metadata to repo-map-derived context and impact payloads so agent consumers
  can see why paths were selected.\n\nKeep the existing files/tests arrays stable, wire the new
  reasons through the actual ranking logic, and update the docs/examples/schema validators to lock
  the contract down.

* feat: add ranked file summaries

Add compact top-level symbol skeletons to context and impact payloads so agents can inspect the
  shape of ranked files without reading full sources.\n\nKeep the new summaries aligned with the
  parser-backed repo-map inventory and update the docs/examples/schema validators to preserve the
  contract.

* feat: boost graph-central context files

Add a lightweight reverse-import centrality bonus to the repo-map ranker so files with more
  downstream dependents surface ahead of otherwise tied leaf importers.\n\nExpose the new
  graph-centrality provenance label through the existing ranking metadata and lock it down with
  focused MCP coverage.

* feat: add pagerank-style graph scores

Replace the opaque graph-centrality bonus with a small personalized reverse-import PageRank pass and
  surface the resulting graph_score in ranked file matches.\n\nKeep the existing reasons contract,
  document the new field, and validate it through MCP coverage plus the docs/schema tests.

* feat: propagate graph scores into test ranking

Use the ranked file graph signal when scoring related tests so validation targets inherit the
  importance of the files they cover.\n\nKeep the test-match contract additive and validate the new
  ordering with focused MCP coverage.

* feat: add prompt-ready context rendering

Add a deterministic context-render surface that combines ranked repo context, compact file
  summaries, selected source blocks, and a prompt-ready rendered_context string.\n\nExpose it
  through both the CLI and MCP, and lock the new contract down with docs examples plus the existing
  schema and harness validator suites.

* feat: add session and bounded context rendering

Extend the prompt-ready context render surface with session-backed reuse and deterministic render
  budgets for files, sources, symbols, and output characters.\n\nExpose the new controls through the
  CLI and MCP, and update the docs/example/schema validators so external agent integrations can rely
  on the contract.

* feat: add render sections and edit targets

Extend context-render with machine-readable section spans and candidate edit targets so external
  agents can trim, reorder, and plan edits without reparsing the rendered text.\n\nKeep the render
  surface deterministic and validator-backed through the existing docs examples, harness checks, and
  schema tests.

* feat: add render provenance and plan seeds

Link render sections back to ranked file and test provenance, and add a compact edit plan seed so
  downstream agents get a default first edit target and validation test set.\n\nSync the public
  context-render contract, example payload, and schema/doc tests so the new metadata stays
  validator-backed.

* feat: enrich edit plan seeds

Add validation command seeds and normalized confidence scores to context-render plan outputs, and
  keep the public example and schema contract aligned.\n\nAlso add an enterprise acceleration
  roadmap that turns the latest AI, compliance, blast-radius, and GPUDirect feedback into concrete
  workstreams for the repo.

* feat: add compact context render profiles

Add optimize-context and render-profile controls to one-shot and session-backed context rendering,
  including compacted source blocks with line maps and render diagnostics for downstream agent
  workflows.\n\nSync the MCP, CLI, example JSON, and Rust schema contracts so token-optimized render
  output stays validator-backed.

* feat: strip python boilerplate in llm renders

Use Python AST structure to remove leading docstrings and pure pass-only boilerplate from compact
  and llm render profiles while preserving line maps and diagnostics for downstream agent
  workflows.\n\nSync the render example, MCP contract tests, and Rust schema validation so the
  richer diagnostics stay part of the public contract.

* feat: add rewrite audit manifests

Add deterministic rewrite audit manifests with file hashes, applied edit ids, checkpoint linkage,
  and validation results for native apply flows, plus MCP passthrough support for the new manifest
  flag.\n\nSync the public apply+verify example, schema validation, and MCP contract tests so the
  audit surface stays validator-backed.

* feat: chain rewrite audit manifests

Add manifest self-digests and previous-manifest hash chaining so rewrite audit artifacts become
  tamper-evident across repeated writes to the same manifest path.\n\nDocument the on-disk digest
  behavior and keep the focused audit-manifest CLI test aligned with the canonical serialization
  used for hashing.

* feat: sign rewrite audit manifests

Add optional HMAC-SHA256 signing for rewrite audit manifests via --audit-signing-key, expose signing
  status in the apply JSON summary, and forward the flag through the MCP rewrite-apply
  surface.\n\nSync the public apply+verify example, schema validation, and focused Rust/MCP tests so
  signed manifests remain part of the validated contract.

* feat: add blast radius symbol analysis

Add a first-class blast-radius surface over the existing symbol and reverse-import graph with
  explicit max-depth controls, ranked file/test metadata, and a rendered caller tree for agent
  consumers.

Wire the new contract through the CLI, MCP server, docs, example payloads, and schema/help tests so
  the repo keeps treating edit-context behavior as validator-backed public API.

* feat: add session blast radius support

Add cached-session blast-radius support across the CLI session subcommands, session stream dispatch,
  and MCP tool surface so agents can reuse the repo-map cache for transitive impact analysis.

Also stabilize the CPU throughput e2e guard by turning it into a bounded sanity floor with warmup
  and retries, which keeps the full suite green on loaded developer machines without pretending to
  be a benchmark.

* feat: add blast radius render workflows

Add prompt-ready blast-radius render surfaces across the CLI, cached-session commands, MCP tools,
  and session stream dispatch so downstream agents can request sectioned transitive impact bundles
  instead of reconstructing them from raw graph payloads.

Publish the new contract through docs, example JSON, and schema/help tests so the render surface
  stays validator-backed like the rest of the harness API.

* feat: verify rewrite audit manifests

Add a first-class audit-manifest verifier for the CLI and MCP surfaces so signed rewrite manifests
  can be checked for digest, chain, and HMAC validity.

Also document the new contract, add a committed example payload, and extend the Python and Rust
  validator suites so the verification surface stays aligned with the native rewrite audit format.

* feat: add native audit manifest verification

Add a native Rust audit-verify subcommand that validates rewrite audit manifest digests,
  previous-manifest chaining, and optional HMAC signatures using the same canonicalization rules as
  manifest emission.

Also cover the new control-plane path with direct tg CLI tests and update the older audit-manifest
  digest assertions to match the canonical manifest format that excludes signature fields from the
  self-digest.

* feat: add built-in security rule packs

Add a built-in ruleset registry plus scan --ruleset support so tensor-grep can run preview
  security/compliance AST packs without requiring an sgconfig project.

This lands the first crypto-safe pack, a rulesets discovery command, and CLI tests that lock the
  built-in scan path to the existing AST scan control plane.

* feat: add structured ruleset scan surfaces

Add JSON output for built-in CLI ruleset scans and expose matching MCP tools for ruleset discovery
  and execution. Keep the AST scan behavior shared between CLI and MCP, then cover the new
  structured payloads with focused unit tests and the full required repo gates.

* feat: add secrets ruleset pack

Add a second built-in security ruleset for obvious hardcoded secret assignments so the new discovery
  and scan surfaces cover more than one preview pack. Keep the patterns intentionally simple and
  validator-backed with focused ruleset tests, then rerun the required repo gates before landing.

* feat: include ruleset finding metadata

Preserve built-in ruleset severity and remediation text in the structured scan findings so CLI and
  MCP consumers can act on scan output directly. Cover the richer payload shape with focused JSON
  assertions and rerun the full required repo gates before landing.

* docs: cover ruleset JSON contracts

Document the new ruleset discovery and built-in ruleset scan JSON shapes, add committed examples,
  and extend the validator-backed doc and Rust schema tests to enforce them. Also format the touched
  Rust tests so the contract line stays green under cargo fmt and the required repo gates.

* feat: fingerprint ruleset findings

Add deterministic SHA-256 fingerprints to structured ruleset scan findings so downstream agents can
  dedupe and track findings across runs. Prove the exact hash shape in focused CLI and MCP JSON
  tests, then rerun the full required repo gates before landing.

* feat: add ruleset finding evidence

Extend structured ruleset scan findings with per-file evidence rows and keep the public contract
  aligned through docs, examples, and schema validation. Prove the richer payload in focused CLI and
  MCP tests, then rerun cargo fmt, the Rust schema validator, and the full required repo gates
  before landing.

* feat: add ruleset scan baselines

Add baseline compare and write support to structured ruleset scans so CLI and MCP consumers can
  classify findings as new, existing, clear, or resolved across runs. Keep the additive payload
  documented and validator-backed, then rerun cargo fmt, the Rust schema validator, and the full
  required repo gates before landing.

* feat: add tls-safe ruleset pack

Add a third built-in preview security ruleset focused on obvious TLS certificate verification bypass
  patterns so the structured scan, evidence, fingerprint, and baseline surfaces cover another
  high-value security class. Validate it with focused ruleset tests plus the full required repo
  gates before landing.

* feat: add ruleset scan suppressions

Add suppression fingerprint support on top of the ruleset scan baseline model so CLI and MCP
  consumers can mark accepted findings as suppressed without reimplementing lifecycle logic. Keep
  the additive payload documented and validator-backed, then rerun cargo fmt, the Rust schema
  validator, and the full required repo gates before landing.

* feat: export ruleset suppressions

Add write-suppressions support to CLI and MCP ruleset scans, emit structured suppressions_written
  metadata, and extend the public JSON/schema contract to cover the new export path.

* feat: add ruleset evidence snippets

Add opt-in bounded evidence snippets for structured ruleset scans, expose the controls through CLI
  and MCP, and extend the public JSON/schema contract for the new snippet payloads.

* feat: expand secrets ruleset coverage

Broaden the built-in secrets-basic pack with API key and token literals across the supported
  languages, update the committed ruleset metadata example, and add focused scan coverage for the
  new JavaScript API key rule.

* feat: expand tls ruleset coverage

Broaden the built-in tls-safe pack with an explicit requests.post verify=False rule, sync the
  committed ruleset metadata example, and add focused scan coverage for the new Python TLS bypass
  pattern.

* chore: update worker skill and user-testing library for editor mission

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

* feat: tighten repo map span and import resolution

Preserve precise edit-planning context across Python, JS/TS, and Rust by recording symbol spans and
  resolving alias/import graph links more accurately.

* feat: synthesize multi-language validation commands

* feat: enrich edit plan seed metadata

Add span, dependency, ordering, and rollback-risk enrichment to repo_map edit plan seeds so editor
  surfaces get deterministic change scope guidance without breaking existing fields.

* test: cover edit plan seed parity surfaces

* feat: strip compact render boilerplate across JS/TS/Rust

* feat: pack context renders to token budgets

* feat: incrementally refresh cached repo maps

Keep session refreshes on the delta path so unchanged files reuse their cached repo-map entries
  while refresh metadata reports the exact filesystem changeset.

* feat: cache multi-root session serve payloads

* feat: benchmark editor-plane runtime paths

Add editor-plane benchmark suites so context rendering, blast-radius rendering, and session refresh
  paths can be measured and regression-checked with stable JSON artifacts.

* feat: track audit manifest history

* feat: add semantic audit manifest diffs

* feat: add preview security rule packs

Add auth-safe, deserialization-safe, and subprocess-safe built-in packs with multi-language scan
  coverage and regression tests for ruleset listing, findings, baselines, suppressions, and evidence
  snippets.

* feat: improve ruleset suppressions

* feat: guard rewrite apply with policy checks

* feat: add enterprise review bundle workflows

Bundle audit, scan, checkpoint, and diff artifacts into a verifiable JSON package so regulated
  review flows can ship one integrity-checked handoff.

* feat: align trust cli and mcp parity

Wrap audit history and diff JSON outputs in the standard envelope so the new trust surfaces match
  existing harness contracts. Add parity regression coverage for help listings, MCP discoverability,
  dispatch routing, and unchanged scan output.

* chore: add scrutiny synthesis report for trust-enterprise

* feat: add machine-readable edit planning surfaces

Add dedicated edit-plan and blast-radius-plan CLI, session, and MCP surfaces on top of the existing
  repo-map planning logic. Also sync the public harness docs, committed JSON examples, and
  schema-backed validators so the new planning contracts remain explicit and stable for external
  agent backends.

* feat: rank edit spans and validation plans

Add ranked span anchors to edit-planning payloads and emit structured validation plans with narrower
  JS/TS/Python/Rust test commands. Also sync the committed JSON examples, harness docs, and
  schema-backed validators to the richer planning contract.

* feat: resolve aliased js and rust definitions

* feat: rank caller-linked tests by import graph

* feat: auto-refresh stale one-shot sessions

* feat: expose session serve health and stats

* feat: add serve cache provenance

* feat: add warm session daemon routing

* feat: add plan provenance and suggested edits

* feat: add blast graph provenance

* feat: standardize session mcp errors

* feat: extend symbol graph provenance

* feat: add import edge provenance

* feat: add test association trust metadata

* feat: add query evidence coverage counts

* feat: add evidence coverage ratios

* feat: summarize blast radius edge trust

* feat: surface blast radius graph trust summary

* feat: surface trust metadata on symbol navigation

* feat: surface trust metadata on planning surfaces

* feat: target exact import edits in plans

Surface import-update suggested edits with parser-backed or heuristic line spans so edit plans can
  point at the dependent import statement that must change.

* feat: target exact caller update lines

* feat: resolve advanced js/ts import chains

Improve JS/TS symbol resolution so default imports, barrel re-exports, and tsconfig aliases still
  map callers and import updates back to original definitions.

* feat: resolve rust workspace module imports

* feat: recognize framework-specific validation test targets

* feat: add repo map profiling phases

* feat: wire editor profiling surfaces

* feat: add bakeoff scenario runner

* feat: add bakeoff scenario fixtures

Add deterministic Python, JS/TS, and Rust fixture repos plus scenario manifests for bakeoff
  coverage.

* feat: add bakeoff miss analysis tooling

* feat: add external evaluation orchestration

* feat: expand external evaluation coverage

* feat: improve python dependent-file precision

* feat: add world class evaluation report

* feat: add codex and copilot competitor runners

* feat: harden competitor eval runners

* feat: add codex retry fallback

* feat: add gemini competitor runner

* feat: harden gemini competitor runner

* feat: harden copilot competitor runner

* feat: add semantic lsp navigation

* feat: add lsp rename support

* feat: add external lsp provider mode

* feat: add semantic provider navigation

* feat: extend semantic provider planning

* test: cover semantic provider public surfaces

* feat: extend semantic provider source navigation

* feat: add semantic provider health reporting

* feat: add provider-aware benchmark harnesses

* perf: fail fast after lsp startup timeout

* docs: refresh paper and world class roadmap

* feat: add patch benchmark harness

* feat: harden gemini patch runner

* perf: kill hung gemini patch runs

* feat: add copilot patch runner

* feat: improve rust test targeting

* feat: add patch scorecard renderer

* feat: add real patch benchmark fixtures

* feat: add claude patch runner

* feat: expand real patch benchmark pack

* feat: isolate patch benchmark runners

* feat: improve patch driver contract

* feat: expand click patch scenarios

* feat: normalize model patch output

* feat: repair truncated model patch hunks

* feat: normalize patch bakeoff inputs

* feat: strengthen patch driver diff contract

* feat: prefer direct edits in claude patch runner

* test: expand real patch benchmark pack

* feat: add claude skill ab benchmark

* feat: add claude ab trace artifacts

* feat: trace tg usage in claude ab benchmark

* docs: record claude ab findings and observability plan

* docs: record rejected claude latency shortcut

* feat: classify claude ab response shapes

* docs: extend agent observability roadmap

* feat: add first action timing to claude ab traces

* feat: track post edit deliberation in claude traces

* feat: add claude output contract experiment mode

* feat: add claude task contract experiment mode

* feat: add claude contract matrix benchmark

* feat: add claude matrix scorecard renderer

* feat: add resumable claude matrix runs

* docs: record 3 task claude matrix result

* feat: checkpoint claude matrix experiments

* feat: add record level matrix resume

* feat: add standard engage probe profile

* feat: add resumable claude ab runs

* docs: record rejected standard engage promotion

* test: expand real patch corpus to 12 scenarios

* docs: record 12-scenario claude ab result

* feat: tighten claude ab task engagement

* feat: add resumable competitor patch runners

* docs: record copilot same pack rerun

* fix: bound gemini timeout cleanup on windows

* fix: isolate gemini benchmark home

* feat: add gemini project skill setup

* feat: add gemini skill ab benchmark

* docs: record same pack system scorecard

* feat: stabilize AI handoff and validation pipeline

* fix: address PR8 review findings

* fix: stabilize CI policy and bakeoff tests

* fix: harden CI search and policy fallbacks

* fix: unblock CI policy and native search checks

* test: stabilize CI-specific policy and routing checks

* test: fix cross-platform audit and schema contracts

* test: make benchmark launcher assertions deterministic

* test: fix benchmark launcher source assertion

* test: normalize provider bakeoff windows paths

* test: fix gemini timeout cleanup coverage

* test: normalize cli help and preview formatting

* test: stub lsp provider binaries in unit tests

* test: stub lsp provider command in status test

---------


## v0.32.0 (2026-03-20)

### Bug Fixes

- Fallback benchmark runs to cli launcher
  ([`a5fe544`](https://github.com/oimiragieo/tensor-grep/commit/a5fe5443c611917d0913035159da2a0ac39bf280))

- Four correctness bugs in rewrite verify, overlap ordering, index root, JSON output
  ([`fb53fed`](https://github.com/oimiragieo/tensor-grep/commit/fb53fed764dcd2153a188aef49114d77b79f4dbc))

1. Verify semantics (backend_ast.rs): - Was: re-search with replacement pattern under first file's
  parent, only check (file, line) membership -- could false-pass if replacement text already existed
  or edits spanned multiple directories - Now: byte-level verification -- reads each file
  post-apply, checks exact replacement text at adjusted byte offsets accounting for preceding edit
  length deltas

2. plan_and_apply overlap ordering (backend_ast.rs): - Was: built rewritten file content before
  overlap validation, then wrote pre-validation content even if overlaps were rejected - Now: plan
  edits first, validate overlaps, then build and write content only from the validated edit set via
  apply_edits_to_file()

3. Index root detection (index.rs): - Was: inferred scan root from first indexed file's parent --
  could miss new sibling files in subdirectories - Now: stores explicit root path in TrigramIndex,
  persisted in binary format, used directly for new-file detection in staleness checks

4. JSON output contract (main.rs): - Was: --apply --verify --json emitted two separate JSON
  documents (verification + plan) on stdout -- broken for harness consumers - Now: emits single JSON
  document with plan and verification fields

88 Rust tests, 510 Python tests, all pass.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- Ignore stale benchmark tg binary
  ([`ed49fd3`](https://github.com/oimiragieo/tensor-grep/commit/ed49fd3785bf9eb7e51bb37f81f359e7952b6f60))

- Restore cross-platform CI stability
  ([`2a52ce6`](https://github.com/oimiragieo/tensor-grep/commit/2a52ce6d78ddf7bbcfd18b9c4699859b316cb0a1))

- Restore python update alias
  ([`1ad658b`](https://github.com/oimiragieo/tensor-grep/commit/1ad658b1d078a740848ea3eaeee159f777a8a0f5))

- Restore tg upgrade and update commands
  ([`67b2445`](https://github.com/oimiragieo/tensor-grep/commit/67b2445922ed1c7ffa221267a87619e5d2c788a1))

- Restore top-level cli help
  ([`4838da0`](https://github.com/oimiragieo/tensor-grep/commit/4838da0a505b0ace58ffc7155ad314a597c3c349))

- Stabilize clean-worktree ci paths
  ([`b513b0a`](https://github.com/oimiragieo/tensor-grep/commit/b513b0ae113324c530e525f09e12360dc1c9ecf6))

- Update check_regression command in services.yaml with --current arg
  ([`2826b96`](https://github.com/oimiragieo/tensor-grep/commit/2826b964bc4c4b3d95db325fc7162d7fe2092ec4))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **ast**: Accept ast-grep wrapper in run command
  ([`db2291f`](https://github.com/oimiragieo/tensor-grep/commit/db2291fe58d1566699616563674fe8aecf14a0fe))

- **ast**: Bound shared parsed-source cache
  ([`13123cd`](https://github.com/oimiragieo/tensor-grep/commit/13123cd9019f929521f004dbf5f4a955ae1f13cf))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **ast**: Calibrate parsed-source cache sizing
  ([`a141af8`](https://github.com/oimiragieo/tensor-grep/commit/a141af888f140c091654fccec49ef30a762bde3e))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **ast**: Honor count-only file contract in workflows
  ([`719a7e2`](https://github.com/oimiragieo/tensor-grep/commit/719a7e2e177e00fc24e8fcfac47e491f2cfe5674))

- **ast**: Keep the AST walker focused on file discovery
  ([`4d8f353`](https://github.com/oimiragieo/tensor-grep/commit/4d8f353f1845cb79beb3cfdbb14dd4d4c844612d))

Separate AST path collection from parse+match work so ignore-based walking stays lightweight and
  rayon handles the CPU-bound search phase.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **ast**: Route native backend only for native patterns
  ([`73f4fd2`](https://github.com/oimiragieo/tensor-grep/commit/73f4fd2f798bb6523beaf1c7d24b9541c990c604))

- **ast**: Support multiline wrapper patterns
  ([`3baf91b`](https://github.com/oimiragieo/tensor-grep/commit/3baf91b11704ff9e6916e9ded20a18d2f746974d))

- **benchmark**: Skip cybert when triton is unavailable
  ([`0d3dc01`](https://github.com/oimiragieo/tensor-grep/commit/0d3dc01136a52d3a16904aaa23b8a5b92308dfec))

- **benchmarks**: Add --output flag to run_ast_workflow_benchmarks.py
  ([`8ded52d`](https://github.com/oimiragieo/tensor-grep/commit/8ded52dd3894bc38d99b488404275970522b967d))

Aligns the script interface with the benchmark command shape documented in AGENTS.md. Defaults to
  artifacts/bench_run_ast_workflow_benchmarks.json.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **benchmarks**: Measure the native CPU engine correctly
  ([`c766777`](https://github.com/oimiragieo/tensor-grep/commit/c766777d1568f2c883cbbb678332acb1064d3628))

Refresh the Windows text benchmark baseline from the current local run and tighten the native CPU
  harness around the actual --cpu path.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **benchmarks**: Refresh windows bootstrap baseline
  ([`60ecef1`](https://github.com/oimiragieo/tensor-grep/commit/60ecef1456277b7b2fcb2ab9ee70c462c4592e53))

Refresh the Windows Python bootstrap benchmark baseline from the current harness output and make
  check_regression.py runnable directly from the repo root without relying on site-packages.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **benchmarks**: Split AST workflow benchmark by backend ownership
  ([`f3e759b`](https://github.com/oimiragieo/tensor-grep/commit/f3e759b58a0723cb4bb1f3c8cb222ad5b5f2cd6e))

Route un through native tg.exe (Rust AST backend) and scan/	est through Python bootstrap
  (sidecar-backed). The Rust CLI Scan/Test subcommands accept no args, so --config cannot pass
  through the native binary yet. Each row now records its backend (native vs sidecar). Fixes broken
  exit-code-2 on scan/test and sys.path/argparse issues under pytest.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **benchmarks**: Stabilize sampled benchmark timings
  ([`4049c1a`](https://github.com/oimiragieo/tensor-grep/commit/4049c1a564a1d335bd23720e38b5025051482d7e))

Record median-of-three timing samples per scenario in run_benchmarks.py, keep parity checks separate
  from timed runs, and reject regression comparisons when python versions drift.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **benchmarks**: Target native tg.exe binary instead of python bootstrap
  ([`d8ce671`](https://github.com/oimiragieo/tensor-grep/commit/d8ce671f1e140020e7a4dde0c92c918e5464a5db))

Switch run_benchmarks.py and run_ast_workflow_benchmarks.py to invoke the native tg.exe binary
  directly, matching the real shipped hot path. Removes the in-process Python count-backend shortcut
  from run_benchmarks.py. Both scripts now accept --binary flag for explicit binary selection.
  Updates corresponding test assertions.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **benchmarks**: Update baselines to native binary measurements
  ([`831e765`](https://github.com/oimiragieo/tensor-grep/commit/831e765ad771a6e33575fad394bd55c4ee494df5))

Replace Python-bootstrap baselines with native tg.exe measurements. Previous baselines showed tg
  0.6-1.9s per scenario (Python startup overhead); native binary measures 0.13-0.48s (within 1-10%
  of rg). Add Milestone 1 control plane audit documenting that text search is already fully
  Rust-native.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **ci**: Apply ruff formatting to backend caches
  ([`d33d5ec`](https://github.com/oimiragieo/tensor-grep/commit/d33d5ec43c778855b1e1e8d327483692155195d1))

- **ci**: Apply ruff formatting to cpu prefilter
  ([`cef5273`](https://github.com/oimiragieo/tensor-grep/commit/cef52739f5c5d577eabaaec384647d7a49b98e1a))

- **ci**: Format ast workflow cache helper
  ([`eecbfb1`](https://github.com/oimiragieo/tensor-grep/commit/eecbfb176c056c73d4d2a079788d529a8af651a4))

- **ci**: Format direct ast workflow bootstrap path
  ([`c45fa69`](https://github.com/oimiragieo/tensor-grep/commit/c45fa69005c23f82bb9a42c1066a0f7a122637a1))

- **ci**: Harden install retries and format ast workflow files
  ([`c572449`](https://github.com/oimiragieo/tensor-grep/commit/c57244994272eaabe6955d39770362e0ec4d30b8))

- **cli**: Infer gpu worker metadata from selected routing
  ([`57dd55e`](https://github.com/oimiragieo/tensor-grep/commit/57dd55e26742e78b48dccd72dbce6220fe696a64))

- **cli**: Report matched files for count-only stats
  ([`e889007`](https://github.com/oimiragieo/tensor-grep/commit/e889007500d81536ee3bdeea1bdb90528a916657))

- **cli**: Surface gpu chunk plans without device ids
  ([`3155cbd`](https://github.com/oimiragieo/tensor-grep/commit/3155cbdf854cd6057c05f9df4bfc9b02c3072fd8))

- **cli**: Track matched files for count-only results
  ([`449909b`](https://github.com/oimiragieo/tensor-grep/commit/449909b11c5b2bb8956417209ed356a69eddf0eb))

- **compat**: Restore set dedup in normalize_lines for sorted line-set diff
  ([`e96bbbe`](https://github.com/oimiragieo/tensor-grep/commit/e96bbbe493fa830b890d2d4fe0b48f2d91970a74))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **cpu**: Report files for count-only python fallback
  ([`a13d632`](https://github.com/oimiragieo/tensor-grep/commit/a13d6328c854a00ad088b73447d90e3cc9aeb2e7))

- **cudf**: Count matched files correctly
  ([`32e15fc`](https://github.com/oimiragieo/tensor-grep/commit/32e15fc09029f4ccd634f462976504ccff918fe7))

- **cudf**: Isolate windows spawn workers
  ([`a72505d`](https://github.com/oimiragieo/tensor-grep/commit/a72505d13ccac524c525d8f39dabeda8cb6fd867))

Pin CUDA_VISIBLE_DEVICES before cudf/rmm import in spawned workers and force fresh Windows pool
  children so reused processes cannot contaminate another GPU context.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **cudf**: Report single-worker plan accurately
  ([`9092a60`](https://github.com/oimiragieo/tensor-grep/commit/9092a604bd02ad7ba7de7e4d8175d83743f1b7cf))

- **cybert**: Bound Triton client availability probes
  ([`496148b`](https://github.com/oimiragieo/tensor-grep/commit/496148b5c24344cb742250a1352b56c96545e1c9))

Add explicit Triton HTTP client timeouts so cyBERT availability and inference setup fail fast when
  the server is hanging. Extend cybert backend unit coverage for dependency-missing,
  client-construction-failure, and server-not-live availability paths.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **cybert**: Gate NLP routing on Triton readiness
  ([`5714863`](https://github.com/oimiragieo/tensor-grep/commit/57148639b75b2826d0bfc2607d81ddecad911583))

Require CybertBackend availability checks to confirm runtime deps and the Triton cybert model before
  selecting the NLP backend, so pipeline fallback stays reachable when the server is unavailable.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **cybert**: Make Triton timeout configurable
  ([`fa41844`](https://github.com/oimiragieo/tensor-grep/commit/fa41844d448b625e9355ce60aae2f2d7cf2271ee))

Allow Triton client probes to honor an environment override while tightening timeout assertions so
  both connection and network guards stay covered.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **deps**: Narrow triton nlp extra to http client
  ([`28c52a9`](https://github.com/oimiragieo/tensor-grep/commit/28c52a9da0b23ecc563c40c914ca2bdcd5ecb69a))

- **docs**: Refresh routing routing-policy artifacts
  ([`30e7626`](https://github.com/oimiragieo/tensor-grep/commit/30e762680afd4cad10e906b6057a9ae1a018092f))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **gpu**: Harden sidecar failure paths
  ([`39b1e84`](https://github.com/oimiragieo/tensor-grep/commit/39b1e84efbbf271280ed004ec88408696edd1940))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **json**: Expose matched file metadata
  ([`e64dc31`](https://github.com/oimiragieo/tensor-grep/commit/e64dc316ab19dde3ec3c81842b425ef866e23a29))

- **json**: Include match details in native search output
  ([`791645b`](https://github.com/oimiragieo/tensor-grep/commit/791645bdb3eba7e6512edc25c75597a4fc159fc0))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **json**: Persist aggregated matched file metadata
  ([`45ca12c`](https://github.com/oimiragieo/tensor-grep/commit/45ca12c32090c3151e9f26449a19c8b46e94149e))

- **json**: Unify Rust CLI envelope metadata
  ([`31c03a8`](https://github.com/oimiragieo/tensor-grep/commit/31c03a8413255dd569ca3732998ff374cf1a1fae))

Keep every machine-readable Rust output on the v1 harness contract so search, rewrite, and GPU paths
  always expose the same routing envelope.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **lint**: Move module docstring before from __future__ import in run_compat_checks.py
  ([`9d8962c`](https://github.com/oimiragieo/tensor-grep/commit/9d8962c286ee8ace32d1f1eb6ecb6a25e5977c0e))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **mcp**: Count matched files for count-only results
  ([`41c725e`](https://github.com/oimiragieo/tensor-grep/commit/41c725e1d2d55a3a674ec0c3ffa833b0486284c8))

- **mcp**: Finalize aggregate file metadata
  ([`b6b962e`](https://github.com/oimiragieo/tensor-grep/commit/b6b962ec188cc6fada7f29a8d8a270e62576eab2))

- **mcp**: Include routing in count responses
  ([`575f856`](https://github.com/oimiragieo/tensor-grep/commit/575f8568dbf8b02531260bd346699b500c130a8c))

- **mcp**: Summarize count-only file results
  ([`4b6ca1c`](https://github.com/oimiragieo/tensor-grep/commit/4b6ca1c329cd9833353fa3401da72b47ce54cb8d))

- **mission-v2**: Clarify run_benchmarks.py measures Python bootstrap not tg.exe
  ([`bcbaa12`](https://github.com/oimiragieo/tensor-grep/commit/bcbaa128320be3c410489211b28ec699d3f93137))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **rewrite**: Harden AST encoding safety
  ([`eaabe54`](https://github.com/oimiragieo/tensor-grep/commit/eaabe54f7c069ad5f102c987c1cfa27c078746ed))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **rewrite**: Make AST apply writes atomic
  ([`50ac29b`](https://github.com/oimiragieo/tensor-grep/commit/50ac29bbf4d181906efe4c927bb1bf3937f4b7e3))

Route AST rewrite apply through temp-file writes so failed rewrites do not leave partial content or
  stray .tg_tmp files behind.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **rewrite**: Reject stale AST plans before apply
  ([`174df2f`](https://github.com/oimiragieo/tensor-grep/commit/174df2fb71284cab3e17efd7a5316143ac873591))

Capture per-file mtimes in rewrite plans so apply aborts before writing when any planned file
  changed after planning.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **rg**: Parse count output without json mode
  ([`7fdd944`](https://github.com/oimiragieo/tensor-grep/commit/7fdd9446718787c6af67d9877f2f82c5ecaa4a98))

- **rg**: Preserve matched file paths for count modes
  ([`ab5917d`](https://github.com/oimiragieo/tensor-grep/commit/ab5917de69e66f2443416bb8334de9b041230bf8))

- **rg**: Preserve per-file counts for count output
  ([`ed3d95c`](https://github.com/oimiragieo/tensor-grep/commit/ed3d95cdd427c764f98ec4696e152c47b00e9787))

- **routing**: Preserve backend identity on empty paths
  ([`f343080`](https://github.com/oimiragieo/tensor-grep/commit/f343080238be12671cd248e93a8e18b5d01858e6))

- **routing**: Prioritize explicit gpu routing
  ([`f8ef2f2`](https://github.com/oimiragieo/tensor-grep/commit/f8ef2f2b9dea03599cf1d101ec6b117993527cc5))

Keep explicit --gpu-device-ids searches from being diverted onto the warm-index path and lock the
  routing matrix with dedicated integration coverage.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **ruff**: Preserve default git exclusions
  ([`2f9d33f`](https://github.com/oimiragieo/tensor-grep/commit/2f9d33fce88ffa3278e3323c6fd4e89c8b8a2cc6))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **run**: Report actual ast backend mode
  ([`6268fea`](https://github.com/oimiragieo/tensor-grep/commit/6268feab9191abaf86422863f721c94f514936ea))

- **rust**: Harden replace edge cases
  ([`9dc59bf`](https://github.com/oimiragieo/tensor-grep/commit/9dc59bfe97fd6db3d753d79e310993678239cfc7))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **rust-core**: Harden runtime override path resolution
  ([`0d3bc99`](https://github.com/oimiragieo/tensor-grep/commit/0d3bc9911cc5a3a99b77e73e9bedbb7b817f7950))

Bound runtime-relative binary discovery to four ancestor levels and surface stderr warnings when
  explicit sidecar or ripgrep override paths are invalid.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **rust-core**: Keep plain-text tg paths Python-free
  ([`5181983`](https://github.com/oimiragieo/tensor-grep/commit/5181983363009714a6089e5189e2d021dd30c9e4))

Remove PyO3 auto-initialization so the Rust CLI only boots Python for explicit Python-backed
  subcommands, preserving pure-Rust search/count/replace behavior even when Python is misconfigured.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **rust-core**: Resolve runtime sidecar and rg paths from tg.exe
  ([`6052a1e`](https://github.com/oimiragieo/tensor-grep/commit/6052a1eb272c8df26d896c9d7c67493c45e881cb))

Use current_exe-based lookup plus explicit env overrides so installed binaries no longer depend on
  cargo workspace paths.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **scan**: Report actual ast backend mode
  ([`009d751`](https://github.com/oimiragieo/tensor-grep/commit/009d751694e9c32e3f9d7cddf99bc1a1df1937cd))

- **search**: Keep ndjson stdout clean on binary skips
  ([`97566d9`](https://github.com/oimiragieo/tensor-grep/commit/97566d96de2fc42a599697e0e17bb30e685e73aa))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **search**: Parallelize native many-file directory scans
  ([`4a7e0c6`](https://github.com/oimiragieo/tensor-grep/commit/4a7e0c603c68ee821c005c003ca0d00418bbc69a))

Search files inside the ignore walker so native CPU searches stop paying a full file-collection pass
  and redundant binary pre-opens on many-file workloads.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **search**: Route Python native CPU mode through tg binary
  ([`ce6b4ce`](https://github.com/oimiragieo/tensor-grep/commit/ce6b4ce156408a1d9889cd5af30ed8ad17f649d2))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **test**: Report actual ast backend mode
  ([`0466652`](https://github.com/oimiragieo/tensor-grep/commit/046665220478cd9294c97d15183f293da1b0daca))

- **torch**: Preserve routing metadata on empty pattern
  ([`a9ba583`](https://github.com/oimiragieo/tensor-grep/commit/a9ba5835d4221d01dd80e8133e2e4c1e97dd42d6))

- **torch**: Resolve mypy no-redef in search path
  ([`bd03c4b`](https://github.com/oimiragieo/tensor-grep/commit/bd03c4b7fdca2a750accbb550f24b35a02ca578d))

- **validation**: Deflake scrutiny timing checks
  ([`102f8c6`](https://github.com/oimiragieo/tensor-grep/commit/102f8c65111a8204c3f92c8d49c6e034bb9e8867))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **version**: Derive fallback cli version from pyproject
  ([`0ad3d2a`](https://github.com/oimiragieo/tensor-grep/commit/0ad3d2a34e59c9e58b924e8b70bcbd7aceec2616))

### Build System

- Enable LTO for the Rust release profile
  ([`00eaa68`](https://github.com/oimiragieo/tensor-grep/commit/00eaa68ec4fa45accb4b79df6735b1cca010fe9d))

Add a release-profile regression test and stabilize timing-sensitive Rust validator assertions so
  the release and CUDA release suites pass reliably.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- Fix tar and pyjwt security alerts
  ([`ab8056d`](https://github.com/oimiragieo/tensor-grep/commit/ab8056d85d97e4be9ece3778f91296403d8a7c32))

### Chores

- Add mission artifacts for performance mission (beat ripgrep)
  ([`e63fcb2`](https://github.com/oimiragieo/tensor-grep/commit/e63fcb28fedabff2dc8c82b6689eab089f60a5bc))

- Research: SIMD text search + GPU text search findings - Library: native CPU engine, GPU native
  engine knowledge - Skills: updated rust-worker for grep crates + cudarc - Services: added cuda
  build/test commands - Validation: draft contracts for CPU, GPU, routing areas

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- Add scrutiny synthesis for ast-search-speed and library updates
  ([`ab02bb0`](https://github.com/oimiragieo/tensor-grep/commit/ab02bb04e61b94ce0329a5dee09b59ca4f3808b1))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- Defer VAL-GPU-009 and VAL-GPU-021 to M3 advanced-gpu milestone
  ([`8cb80c4`](https://github.com/oimiragieo/tensor-grep/commit/8cb80c46b00899e2c7a14616510692f5f67f54a2))

GPU crossover requires M3 optimizations (CUDA streams, pinned memory). OOM handling test will be
  added in M3 advanced-gpu-benchmarks. Override M2 user-testing validator: 18/20 passed, 2 deferred.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- Ignore local factory validation artifacts
  ([`a06dcef`](https://github.com/oimiragieo/tensor-grep/commit/a06dceff28a331e41f0254a389f600e713ba3be1))

- Remove stray test scripts from repo root
  ([`9963f62`](https://github.com/oimiragieo/tensor-grep/commit/9963f626f478cea0b728775da84624e67de25c17))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- Update mission artifacts for AST+rewrite speed mission
  ([`23a67e1`](https://github.com/oimiragieo/tensor-grep/commit/23a67e1dd828d3e8b8df33011733cbfd0b21c43e))

- Updated rust-worker skill with AST search and rewrite optimization guidance - Added
  benchmark_parity, rust_test_release, rust_build_release to services.yaml - Updated user-testing.md
  with AST/rewrite benchmark surfaces and baselines

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- Update mission infrastructure for continuation plan
  ([`f7d7e33`](https://github.com/oimiragieo/tensor-grep/commit/f7d7e334b628da5f955f2fbe86831dd0cd54d137))

Update worker skills, services manifest, and library files for the 7-priority continuation mission
  covering JSON contract unification, benchmark expansion, routing hardening, GPU crossover, editor
  safety, index scaling, and harness workflow integration.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **bench**: Refresh Windows regression baseline
  ([`e6f4948`](https://github.com/oimiragieo/tensor-grep/commit/e6f4948bcd08633c5b51aa6dbebd798bcb01b525))

Update the stored Windows text benchmark baseline to match current host measurements before
  regression checks.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **factory**: Track worker infrastructure
  ([`a9e3a5e`](https://github.com/oimiragieo/tensor-grep/commit/a9e3a5e00db7711cbad8c2d629c1de402a0a1d1e))

Version control the mission worker bootstrap and skill definitions so future runs have the required
  local Factory scaffolding.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **mission-v2**: Update .factory/ infrastructure for Rust-first control plane mission
  ([`4c6adfd`](https://github.com/oimiragieo/tensor-grep/commit/4c6adfdd2ec75f9a048383f9defbc98d2b4aae01))

- Updated rust-worker and backend-worker skills with v2 procedures (MSRV 1.79, ast-grep-core, IPC
  sidecar, Windows notes) - Added rust_clippy, rust_check, benchmark_compat, check_regression to
  services.yaml - Extended user-testing.md with v2 Rust/AST/Index/Rewrite validation guidance -
  AGENTS.md created with mission boundaries and architecture contract

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **scrutiny**: Add benchmark-expansion milestone validation
  ([`126a045`](https://github.com/oimiragieo/tensor-grep/commit/126a045d5916a06769c19d6e43f17092d00d660a))

Scrutiny round 1 for benchmark-expansion milestone. All 5 features reviewed, all passed. No blocking
  issues. Validators: pytest 537 passed, mypy clean, ruff clean.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **scrutiny**: Add harness-api milestone scrutiny synthesis and reviews
  ([`cda4acc`](https://github.com/oimiragieo/tensor-grep/commit/cda4accb42e8902236345e5bdae9c444a2cc47e0))

All 4 implementation features passed code review. Validators all green: lint, typecheck, pytest (513
  passed), cargo test (98 passed). No blocking issues found. Library knowledge documented.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **scrutiny**: Add harness-workflow milestone scrutiny validation
  ([`bdba4c2`](https://github.com/oimiragieo/tensor-grep/commit/bdba4c2fafd74bd84c43586cf530de52f6e5b527))

Scrutiny round 1 for milestone harness-workflow: - All validators passed (549 tests, mypy clean,
  ruff clean) - 5/5 feature reviews passed (deferred-gpu-correctness-validation, mcp-rewrite-tools,
  mcp-index-search-tool, ndjson-streaming, batch-rewrite-api) - No blocking issues found - 6
  non-blocking observations recorded - 3 guidance update suggestions for orchestrator

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **scrutiny**: Add misc-1 milestone scrutiny validation reports
  ([`2bdd34b`](https://github.com/oimiragieo/tensor-grep/commit/2bdd34b382528a0e24b33c17a40b0042f3491209))

Scrutiny validation for misc-1 milestone. All validators passed (test 491 passed/14 skipped, mypy
  clean, ruff clean). Both features (refresh-python-benchmark-baseline, benchmark-harness-stability)
  reviewed and passed with no blocking issues.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **scrutiny**: Add misc-2 milestone scrutiny validation reports
  ([`b7c6616`](https://github.com/oimiragieo/tensor-grep/commit/b7c6616581d1d8c4c6caae46c4717aa4b5012d64))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **scrutiny**: Add misc-fixes milestone validation synthesis
  ([`8a9633c`](https://github.com/oimiragieo/tensor-grep/commit/8a9633c4433daac66ef8eeb4c757db097a4dde84))

Round 1 scrutiny: all validators pass (465 tests, mypy clean, ruff clean). Reviewed
  auto-extract-rg-benchmark feature - passed with no blocking issues.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **scrutiny**: Add routing-and-safety milestone scrutiny validation
  ([`fa877d3`](https://github.com/oimiragieo/tensor-grep/commit/fa877d3b735bc77e946c2901c1d88f4ace7b2007))

Validates all 5 implementation features for the routing-and-safety milestone. All validators pass
  (538 pytest, 122 cargo, mypy clean, ruff clean). All 5 feature reviews pass with no blocking
  issues.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **scrutiny**: Add rust-native-control-plane milestone scrutiny validation reports
  ([`454b69b`](https://github.com/oimiragieo/tensor-grep/commit/454b69b8145e4e32ecbf2133bfc07b8a9fa54b03))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **scrutiny**: Gpu-and-index-scaling milestone validation - all 6 features pass
  ([`b966857`](https://github.com/oimiragieo/tensor-grep/commit/b966857f410003b1cfb39d1d5aa6996636ec4bfa))

Ran full test suite (543 Python, 128 Rust tests), typecheck, and lint. Spawned review subagents for
  all 6 implementation features. Applied 2 library updates (index-compression version note, regex
  prefilter docs). 3 guidance suggestions for orchestrator (skill timing, soft-delete docs, Python
  TDD).

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Add compatibility-and-benchmark-parity milestone scrutiny synthesis (round 1 -
  fail)
  ([`90e2fa9`](https://github.com/oimiragieo/tensor-grep/commit/90e2fa9ad6ac7670294a4b0a75b6e535bd0e56f6))

Lint validator failed: 11 E402 errors in benchmarks/run_compat_checks.py. Test (503 passed) and
  typecheck passed. Feature review deferred until lint is fixed.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Add compatibility-and-benchmark-parity milestone scrutiny synthesis (round 2 -
  pass)
  ([`c9df2a0`](https://github.com/oimiragieo/tensor-grep/commit/c9df2a0365a271cf2fa6e81ccb9520dbf93cb41c))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Add compatibility-and-benchmark-parity milestone user testing synthesis (round 1 -
  pass)
  ([`b5cfb97`](https://github.com/oimiragieo/tensor-grep/commit/b5cfb9756da56f2a3bff49e10e412a93b25702da))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Add misc-1 milestone user-testing synthesis report
  ([`4a402b0`](https://github.com/oimiragieo/tensor-grep/commit/4a402b0665ee8fc49d23e5d59e539d7347fe67b8))

No validation contract assertions to test — both misc-1 features (refresh-python-benchmark-baseline,
  benchmark-harness-stability) are infrastructure improvements with empty fulfills fields.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Add misc-2 milestone user-testing synthesis report
  ([`60022ae`](https://github.com/oimiragieo/tensor-grep/commit/60022ae21b745aaf6513369c09c6111dbfb7157b))

No testable assertions for misc-2 milestone - both implementation features have empty fulfills
  arrays.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Add review report for advanced-gpu-benchmarks
  ([`67bcad9`](https://github.com/oimiragieo/tensor-grep/commit/67bcad918b30fd30c7285fe1dccbcd9c313c7564))

- **validation**: Add rust-native-control-plane milestone user-testing synthesis report
  ([`18fae6a`](https://github.com/oimiragieo/tensor-grep/commit/18fae6a19df542e141d2762ecab4b0007c3134e7))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Add scrutiny synthesis for bounded-cache milestone
  ([`b1da3b9`](https://github.com/oimiragieo/tensor-grep/commit/b1da3b9308a40f8b4ce872d51bf0d3701e5ea0ff))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Add scrutiny synthesis for contract-fixes milestone
  ([`a25d12e`](https://github.com/oimiragieo/tensor-grep/commit/a25d12e777d03eae2a88619d8f2ac686fc8dfeed))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Add scrutiny synthesis for misc-config milestone
  ([`d19f7af`](https://github.com/oimiragieo/tensor-grep/commit/d19f7af80ff8905640fcf6376dd2fddfa0cde5b0))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Add scrutiny synthesis for misc-quality milestone
  ([`ff88a64`](https://github.com/oimiragieo/tensor-grep/commit/ff88a644ceac2b56f27a4c83ea69d0f5586ab6f7))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Add scrutiny synthesis for misc-quality-2 milestone
  ([`f20fcbc`](https://github.com/oimiragieo/tensor-grep/commit/f20fcbc8437bf24cce03a2a28ea45ea00462e3e6))

- All validators passed (480 tests, mypy clean, ruff clean) - 2/2 feature reviews passed with no
  blocking issues - Added benchmark_ast, benchmark_ast_workflow, benchmark_gpu to services.yaml

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Add scrutiny synthesis for misc-quality-3 milestone
  ([`7c8e4c4`](https://github.com/oimiragieo/tensor-grep/commit/7c8e4c4ef82f7fef84d96978282fe34dee2fa0d0))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Add scrutiny synthesis for misc-quality-4 milestone
  ([`2bf1e0e`](https://github.com/oimiragieo/tensor-grep/commit/2bf1e0e702ba8b7d8f8c8b8a54a339ef5d5618d0))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Add scrutiny synthesis for misc-quality-5 milestone
  ([`fdf52cf`](https://github.com/oimiragieo/tensor-grep/commit/fdf52cf47cfc4074e8e583e087bf22c7f87ee9a5))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Add scrutiny synthesis for rust-hot-path milestone
  ([`3ce4c39`](https://github.com/oimiragieo/tensor-grep/commit/3ce4c3977af2cea2dee2a5acc23a161495bb6c59))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Add scrutiny synthesis report for advanced-gpu
  ([`d21f404`](https://github.com/oimiragieo/tensor-grep/commit/d21f404341efdda8123551b5fcef2cb83d40d9c9))

- **validation**: Add scrutiny synthesis report for native-cpu-engine
  ([`f4dd12d`](https://github.com/oimiragieo/tensor-grep/commit/f4dd12d1ecc336c5fc0b0adfdca6353e557e333c))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Add scrutiny synthesis report for native-cpu-engine (round 2)
  ([`231faf5`](https://github.com/oimiragieo/tensor-grep/commit/231faf5b7e3aaf4ab009f088517de010d0e71833))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Add scrutiny synthesis report for native-gpu-engine (round 1)
  ([`1658928`](https://github.com/oimiragieo/tensor-grep/commit/165892862d2c53e1f9116e315ad3b8d8127eea52))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Add scrutiny synthesis report for routing-and-integration
  ([`35159cc`](https://github.com/oimiragieo/tensor-grep/commit/35159cc52eb6624e73f30a69672c864964c6b89a))

- **validation**: Add scrutiny synthesis round 2 for contract-fixes milestone
  ([`d8ad262`](https://github.com/oimiragieo/tensor-grep/commit/d8ad262163a186ccee278994d0c7c62b4cfc27a6))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Add user testing synthesis for advanced-gpu milestone
  ([`8943e7d`](https://github.com/oimiragieo/tensor-grep/commit/8943e7dc5cc8f57718fc617559ff00eddcff8203))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Add user testing synthesis for routing-and-integration milestone
  ([`8ce7443`](https://github.com/oimiragieo/tensor-grep/commit/8ce744345b11488d6f2519066afa7a8ef1ee85a8))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Add user testing synthesis report for native-cpu-engine
  ([`5d25c34`](https://github.com/oimiragieo/tensor-grep/commit/5d25c343f74b76c30b37fca6b8e079e9caa6c265))

- **validation**: Add user testing synthesis report for native-cpu-engine (round 2)
  ([`8b43442`](https://github.com/oimiragieo/tensor-grep/commit/8b43442842a51134afa8ec2d98d614b6a4359d3f))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Add user testing synthesis report for native-cpu-engine (round 3)
  ([`25cb8b8`](https://github.com/oimiragieo/tensor-grep/commit/25cb8b8b7c262aff6a7fa01adc6c18d7a1aae7c9))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Add user testing synthesis report for native-gpu-engine (round 1)
  ([`9942b48`](https://github.com/oimiragieo/tensor-grep/commit/9942b484f05ef492e6796bde2556772da2d1d3c2))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Add user-testing synthesis for bounded-cache milestone
  ([`98875ec`](https://github.com/oimiragieo/tensor-grep/commit/98875ec6f2dab2d4951c511d54671b7b105caf73))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Add user-testing synthesis for contract-fixes milestone
  ([`923221c`](https://github.com/oimiragieo/tensor-grep/commit/923221c14ea6c5c4c8200cacb1e0e88d38918de2))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Add user-testing synthesis for misc-config milestone
  ([`e894744`](https://github.com/oimiragieo/tensor-grep/commit/e89474431c64bd1825aeef4c411ffabd159e6a0d))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Add user-testing synthesis for misc-fixes milestone
  ([`3ad674f`](https://github.com/oimiragieo/tensor-grep/commit/3ad674f10e5ad4118867f13e91289f9817e2d2a9))

No testable assertions for this milestone - both features have empty fulfills arrays. Environment
  gates verified: pytest 465 passed, ruff clean, mypy clean.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Add user-testing synthesis for misc-quality milestone
  ([`70903de`](https://github.com/oimiragieo/tensor-grep/commit/70903de7fa3aad3b2e8ce90edfce24dbba71ab18))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Add user-testing synthesis for misc-quality-2 milestone
  ([`c369fbe`](https://github.com/oimiragieo/tensor-grep/commit/c369fbe1f5edc7dbf5cb3c32bfffad49df37dae8))

No testable assertions for this milestone - all 9 contract assertions already passed from prior
  milestones and implementation features have empty fulfills arrays.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Add user-testing synthesis for misc-quality-3 milestone
  ([`ce6cdf2`](https://github.com/oimiragieo/tensor-grep/commit/ce6cdf2665217367d7806240a2b7e1babdedb0fe))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Add user-testing synthesis for misc-quality-4 milestone
  ([`8993298`](https://github.com/oimiragieo/tensor-grep/commit/8993298ca52ec5a02a689f956102689c81efc360))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Add user-testing synthesis for misc-quality-5 milestone
  ([`63a2927`](https://github.com/oimiragieo/tensor-grep/commit/63a2927fd1d36dd4e0eedee2ff7e8f4fd1343b14))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Add user-testing synthesis for rust-hot-path milestone
  ([`511da53`](https://github.com/oimiragieo/tensor-grep/commit/511da5366c7ff1370b5fcc8791f3168cc2affee0))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Add user-testing synthesis for worker-safety milestone
  ([`308ffa4`](https://github.com/oimiragieo/tensor-grep/commit/308ffa404cdcdf32ea9a1002e8a7b0da931deb2f))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Add user-testing synthesis round 2 for contract-fixes milestone
  ([`57dfaec`](https://github.com/oimiragieo/tensor-grep/commit/57dfaec1ef9835ce9e055d24453919b148e0ccfe))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Add worker-safety milestone scrutiny synthesis
  ([`577c467`](https://github.com/oimiragieo/tensor-grep/commit/577c4675fa7f5968c5d0982240386301a1fe79c2))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Synthesize rewrite-apply-speed scrutiny results
  ([`70b907e`](https://github.com/oimiragieo/tensor-grep/commit/70b907e786a49928943b51f6f805784c0789c940))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Synthesize rewrite-apply-speed user-testing results
  ([`9515a40`](https://github.com/oimiragieo/tensor-grep/commit/9515a40abde4a249df88992c27614a7a7221d06a))

- **validation**: User testing for benchmark-expansion milestone — all 11 assertions passed
  ([`6c87c74`](https://github.com/oimiragieo/tensor-grep/commit/6c87c745f5f2edb9c719583278141e6226b6bdf5))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: User testing synthesis for harness-workflow milestone - all 24 assertions passed
  ([`a1a125a`](https://github.com/oimiragieo/tensor-grep/commit/a1a125a38b74de5b3b88261b95f767496c2ae673))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

### Continuous Integration

- Add scheduled and native build smoke coverage
  ([`868f176`](https://github.com/oimiragieo/tensor-grep/commit/868f176b26458ee7d637f87821e3d09b1ffa9b3a))

### Documentation

- Add continuation plan for next agent handoff
  ([`1e2fbb3`](https://github.com/oimiragieo/tensor-grep/commit/1e2fbb3b8cad00a2af644dfffd8d9b81db4b4fa8))

Seven prioritized work items with exact file paths, commands, and invariants to preserve. Documents
  current state, accepted performance lines, architecture, CLI surface, and explicit anti-patterns
  to avoid.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- Close Milestone 1 audit — all gaps resolved
  ([`d6e5e82`](https://github.com/oimiragieo/tensor-grep/commit/d6e5e82f33b2c646da24ccecced39754f624fe09))

GPU sidecar routing done (2f8f96b), benchmark surfaces aligned to native binary (d8ce671, 831e765,
  f3e759b, 8ded52d), pipeline.py lazy imports evaluated and rejected (test churn vs negligible
  payoff). Next priority shifts to product performance: native AST speed, hot-path recovery.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- Finalize post-mission contracts and benchmark refresh
  ([`c3c4913`](https://github.com/oimiragieo/tensor-grep/commit/c3c4913b8dbf40c257e1e7c19a82965822892d13))

- Improve top-level cli help
  ([`aef1063`](https://github.com/oimiragieo/tensor-grep/commit/aef10637cd7e797f75f7fe1c1d500f1aa51e6256))

- Update README and PAPER with AST search/rewrite speed results
  ([`8cbefb7`](https://github.com/oimiragieo/tensor-grep/commit/8cbefb75683224d5a5b32e066dd779046e69ad2e))

- AST search ratio (tg/sg): 0.795x (tg ~20% faster than sg) - Rewrite apply ratio: 0.848x (1000
  files), 0.851x (5000 files) - 40/40 structural match parity across Python, JS, TS, Rust - Document
  8 key optimizations (LTO, pre-filter, fused IO, direct writes) - Update rewrite write strategy
  from atomic temp+rename to direct writes

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- Update README with continuation mission results
  ([`2418e0f`](https://github.com/oimiragieo/tensor-grep/commit/2418e0f2395fe9c4a747869f96f3fc3c880b52d5))

Updated README to reflect completed 5-milestone mission: - Unified JSON harness API with schema docs
  and compat tests - Multi-language benchmark suite (JS/TS/Rust corpus generators) - Editor safety:
  atomic writes, stale-file detection, encoding safety - Index subsystem: varint compression (73.5%
  reduction), incremental updates, regex improvements - Harness workflow: MCP rewrite/index tools,
  NDJSON streaming, batch rewrite API - GPU crossover benchmarks and sidecar error hardening - 145
  Rust tests, 549+ Python tests

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- Update README with performance mission results
  ([`ada8cde`](https://github.com/oimiragieo/tensor-grep/commit/ada8cde2d0c96c9c6c80082ac153c41fa36b71ed))

- Native CPU engine: 2-4x faster than rg on large files - Native GPU engine: 64.2x at 100MB via
  cudarc + NVRTC - Multi-GPU: 49.5% improvement with dual GPU - New CLI: --cpu, --gpu-device-ids, -e
  multi-pattern, tg calibrate - Smart routing with measured crossover calibration - 572 Python
  tests, 200+ Rust tests

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **.factory/skills**: Add dirty-checkout strategy and Windows guidance to worker skills
  ([`9efbff5`](https://github.com/oimiragieo/tensor-grep/commit/9efbff54ebcef96946527b5ae81af6b9a493f4ec))

- **agents**: Add repo CI and benchmark rules
  ([`1ab516f`](https://github.com/oimiragieo/tensor-grep/commit/1ab516fd45ce7c11d629988e1113fc8f4cb245c8))

- **agents**: Add repo CI and benchmark rules
  ([`d792020`](https://github.com/oimiragieo/tensor-grep/commit/d792020a3e19f76f5014e8aa5635abd84141b713))

- **benchmark**: Lock local benchmark install contract
  ([`ffe1b02`](https://github.com/oimiragieo/tensor-grep/commit/ffe1b0226999b8e9d899368b4dfff2bb4af88e54))

- **benchmark**: Refresh latest benchmark and parity contracts
  ([`a0a10d5`](https://github.com/oimiragieo/tensor-grep/commit/a0a10d5e12a454450b99cf0532833de8c8f5a5ed))

- **benchmark**: Refresh results for 2026-03-09 run
  ([`9a9a365`](https://github.com/oimiragieo/tensor-grep/commit/9a9a36505f4411018250955e8a8f79b73dfa7b10))

- **benchmarks**: Record accepted AST line and add output stability sort
  ([`fdc6191`](https://github.com/oimiragieo/tensor-grep/commit/fdc6191e1a4a80298cfe615baacc61707679c97e))

Add deterministic sort by (file, line) to parallel AST search output. Update benchmarks_ast.md with
  current accepted line (tg 325ms vs sg 444ms, 1.37x faster). Record the accepted line in PAPER.md.
  Parity verified: 40/40 cases across Python, JS, TS, Rust.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **harness**: Add JSON contract reference artifacts
  ([`9130166`](https://github.com/oimiragieo/tensor-grep/commit/91301665d67cf679714e2aef3f90d07dd61ed260))

Document the native harness API shapes and commit generated example payloads so future contract
  checks have realistic fixtures.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **mission-v2**: Update library with TG_RG_PATH, bench_data gitignore, hyperfine notes
  ([`d6ef1c7`](https://github.com/oimiragieo/tensor-grep/commit/d6ef1c7cb100577d4eddff98e98e3c116ed83509))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **mission-v2**: Update rust-worker skill with venv and rg.exe runtime notes
  ([`ff14ef2`](https://github.com/oimiragieo/tensor-grep/commit/ff14ef29bc2eb03bafd3faa713eb94f3bbd9d036))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **paper**: Capture next-phase architectural direction and research from 2026-03 analysis
  ([`9c2f002`](https://github.com/oimiragieo/tensor-grep/commit/9c2f002fb0573f5341a3f11d6b3b12728f0cecce))

- **paper**: Record optimization ledger and rejected attempts
  ([`2ba21be`](https://github.com/oimiragieo/tensor-grep/commit/2ba21bee2413673c4361148df4cb1b037818d2c7))

- **paper**: Record optimization ledger and rejected attempts
  ([`2b67a2a`](https://github.com/oimiragieo/tensor-grep/commit/2b67a2ae82b1decc07c16bb338e6fb09980ed622))

- **README**: Reflect mission improvements
  ([`93e1380`](https://github.com/oimiragieo/tensor-grep/commit/93e13806c78aaa49ad52dce7ebe20fb527f4dc96))

- **routing**: Document current backend selection policy
  ([`d55fef3`](https://github.com/oimiragieo/tensor-grep/commit/d55fef346cf13548e7163347ebd963581fc6193a))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

### Features

- Auto-route to warm index, post-apply verification, e2e harness test
  ([`f841fe9`](https://github.com/oimiragieo/tensor-grep/commit/f841fe99447631ae310ccfbf6b81a4c1487a3c84))

Routing policy: - Auto-detect warm .tg_index when searching (no --index needed) - Conservative: only
  activates when index exists, is not stale, pattern >= 3 chars, and no unsupported flags
  (invert/context/ max_count/word_regexp/globs) - Falls through to rg for cold path

Post-apply verification: - RewritePlan::verify() re-searches with replacement pattern - Reports
  verified count and mismatches with edit IDs - tg run --rewrite --apply --verify for CLI
  verification - VerifyResult/VerifyMismatch serializable for harness consumption

End-to-end harness flow test: - search -> plan -> diff -> apply -> verify in one test - Confirms the
  full agent workflow: find matches, build plan, preview diff, apply edits, verify correctness

88 total Rust tests, 510 Python tests, all pass.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **ast**: Parallelize AST search walk filtering
  ([`4936ec1`](https://github.com/oimiragieo/tensor-grep/commit/4936ec1f1e8a6407a032776bdc5f860f607ed4c1))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **benchmarks**: Add AST benchmark gate, parity check, and corpus generator
  ([`b8720da`](https://github.com/oimiragieo/tensor-grep/commit/b8720da6e552fbb402961e50e3fbe71059427a2a))

Rewrite run_ast_benchmarks.py to use hyperfine for M3 AST cold-start gate. Add gen_corpus.py for
  deterministic benchmark corpus generation (Python AST bench + multi-language parity). Add
  run_ast_parity_check.py for 40-case tg vs ast-grep parity validation. Optimize Rust backend_ast.rs
  line number lookups via precomputed line-start offsets.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **benchmarks**: Add harness loop benchmark
  ([`8531aac`](https://github.com/oimiragieo/tensor-grep/commit/8531aac1da89ae7889e16c25a9dd91ef1efd4a80))

Benchmark the full AST agent cycle by measuring repeated search, plan, apply, and verification
  timings with corpus restoration between iterations.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **benchmarks**: Add index scaling benchmark contract
  ([`8eed353`](https://github.com/oimiragieo/tensor-grep/commit/8eed3532e598f4dcc19a4102ed61425631eca478))

Measure indexed search build/query scaling at 1k, 5k, and 10k files and lock benchmark JSON
  artifacts to the required suite/timestamp fields.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **benchmarks**: Add milestone regression gates
  ([`f416b2a`](https://github.com/oimiragieo/tensor-grep/commit/f416b2aa902865b2e7e36556e8b3775b733d4cfa))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **benchmarks**: Add multi-language AST benchmark gate
  ([`4fde376`](https://github.com/oimiragieo/tensor-grep/commit/4fde376f4d42c79f2565df183273818933c5f5c2))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **benchmarks**: Add multi-language AST corpus generation
  ([`341cf5d`](https://github.com/oimiragieo/tensor-grep/commit/341cf5d7a552f78baf665f032e6ff79e210a19f4))

Add JavaScript, TypeScript, and Rust ast-bench corpus generation while keeping the default Python
  output path backward compatible.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **benchmarks**: Add native CPU benchmark coverage
  ([`2c14cff`](https://github.com/oimiragieo/tensor-grep/commit/2c14cff62cd8b8f5379bd81c16cbf1ff3ef68307))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **benchmarks**: Measure GPU crossover at scale
  ([`7e36e14`](https://github.com/oimiragieo/tensor-grep/commit/7e36e14a8586d5a4e33e84030eb76483add831d8))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **benchmarks**: Support large rewrite benchmark runs
  ([`375e19e`](https://github.com/oimiragieo/tensor-grep/commit/375e19e374fd3fa74dc5b83e1de89a7211a5f087))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **gpu**: Adapt long-line searches to warp and block kernels
  ([`0b0b98a`](https://github.com/oimiragieo/tensor-grep/commit/0b0b98abd768cccc168fa79897a9bb7dece0a491))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **gpu**: Add --gpu-device-ids flag with sidecar routing
  ([`2f8f96b`](https://github.com/oimiragieo/tensor-grep/commit/2f8f96bf8f61da83baf5c373896ab7b82b73c615))

Add --gpu-device-ids to both positional and search subcommand CLIs in the native Rust binary. When
  present, the binary sends a gpu_search command through the existing JSON-over-stdio sidecar
  protocol to the Python side, which constructs a Pipeline with explicit GPU device IDs and
  dispatches to the appropriate GPU backend (cuDF/Torch).

Fails loudly with a clear ConfigurationError when GPU backends are unavailable, matching the
  explicit GPU contract violation behavior.

The hot path (plain text search without --gpu-device-ids) is unchanged: the guard is an empty-Vec
  check that short-circuits immediately.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **gpu**: Add advanced native benchmark reporting
  ([`1642bba`](https://github.com/oimiragieo/tensor-grep/commit/1642bba367b97e553f273696e6c9945965c3cf30))

Expose advanced native GPU benchmark hooks for internal pipeline metrics and extend the Python
  benchmark harness to validate throughput, multi-GPU, transfer, CUDA graph, and OOM assertions with
  recorded evidence.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **gpu**: Add cudarc native search foundation
  ([`49f2dfd`](https://github.com/oimiragieo/tensor-grep/commit/49f2dfd74157697e6f47da75c8e2af8269bf0f42))

Enable a feature-gated native CUDA substring search path so later routing work can use
  NVRTC-compiled kernels and enumerate GPUs without requiring CUDA at build time.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **gpu**: Auto-route large native searches
  ([`1bc450b`](https://github.com/oimiragieo/tensor-grep/commit/1bc450bef3d408f875d6c77661f058a9f64607a9))

Prefer native GPU routing for large eligible searches while preserving small-search passthrough
  performance. Add graceful CPU fallback for unavailable CUDA contexts, user-facing init failures,
  and routing tests for threshold behavior.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **gpu**: Batch multi-pattern native searches
  ([`a842ace`](https://github.com/oimiragieo/tensor-grep/commit/a842ace625e7cd4419ad2a72fad589af9b88ce9c))

Run repeated -e patterns through a shared-memory CUDA dispatch when they fit, and preserve
  pattern-aware JSON output plus fallback batching for larger sets.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **gpu**: Benchmark CUDA graph replays for batched searches
  ([`72eee88`](https://github.com/oimiragieo/tensor-grep/commit/72eee88d1588675984efdf928ad545eadd3f2e31))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **gpu**: Benchmark native crossover and error paths
  ([`c482d24`](https://github.com/oimiragieo/tensor-grep/commit/c482d24da60354df106b5cc7139cec3ca193d5ca))

Capture measured native GPU crossover data across 10MB-1GB corpora and lock the
  benchmark/error-handling contracts so routing decisions stay grounded in real results.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **gpu**: Overlap native transfers with pinned streams
  ([`e6954f8`](https://github.com/oimiragieo/tensor-grep/commit/e6954f8c00ba270b246c7235d6829ce810faf983))

Use pinned host buffers and a two-stream double-buffered pipeline so the native CUDA path can
  overlap host-to-device copies with kernel execution. Add coverage for pinned allocation, overlap
  correctness, stream metrics, and the 1 GiB transfer throughput gate.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **gpu**: Route explicit GPU searches to native batching
  ([`9fbf6f8`](https://github.com/oimiragieo/tensor-grep/commit/9fbf6f8d7ea551dd237ff16bf81e09b53c175fa7))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **gpu**: Split native search across selected devices
  ([`a372420`](https://github.com/oimiragieo/tensor-grep/commit/a372420c992e07071f8afa60c7cba8acefc1e835))

Drive explicit --gpu-device-ids searches across multiple CUDA contexts concurrently so both GPUs
  participate while preserving ordered, deduplicated results.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **index**: Add incremental stale-index refresh
  ([`8a004db`](https://github.com/oimiragieo/tensor-grep/commit/8a004dbb87b791b787fc7bf3c04405858dce126e))

Reuse unchanged trigram postings when indexed files are added, removed, or modified so stale index
  rebuilds do less work and expose clear rebuild-mode telemetry.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **index**: Add native trigram index subsystem for repeated-query acceleration
  ([`289ea8c`](https://github.com/oimiragieo/tensor-grep/commit/289ea8c8b46fdf7aeb7b730ac56509211336dd71))

New Rust index module (index.rs) with: - Per-file trigram extraction via mmap with rayon parallelism
  - Compact binary serialization (TGI1 format) for fast load - File metadata (path, mtime, size) for
  staleness detection - Automatic index rebuild when corpus changes - Posting list intersection for
  candidate filtering - Regex support via longest-literal extraction for trigram prefilter -
  Verification pass against actual file content (no false positives)

CLI: tg search --index enables index-accelerated search. First query builds the index; subsequent
  queries reuse it. Verbose mode reports index build/load/routing metadata.

Performance (1000 files, 50k LOC benchmark corpus): - Index build: 513ms (one-time) - Warm query:
  84-160ms (vs 238ms cold rg scan, up to 2.8x faster) - Repeated queries show ~1.2x consistent
  improvement

14 tests: 7 unit tests (build, search, persistence, staleness, case-insensitive, regex,
  short-pattern) + 7 integration tests (CLI build, count, JSON, verbose, cache reuse, no-match,
  case-insensitive).

73 total Rust tests, 510 Python tests, all pass.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **index**: Compress posting-list format
  ([`98f153f`](https://github.com/oimiragieo/tensor-grep/commit/98f153fafb919f76aef36af8d935a6caca9de681))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **index**: Harden invalidation, format versioning, harness JSON contract
  ([`2784b98`](https://github.com/oimiragieo/tensor-grep/commit/2784b985abc58fd5f9e2f35488c568b71ecc03b4))

Index hardening: - Format version byte (v1) in binary header after magic - Staleness detection for
  content change, file deletion, new file added - staleness_reason() returns diagnostic string for
  verbose output - Reject bad magic, future format versions, truncated files - Graceful recovery
  from corrupt index (auto-rebuild) - build_with_options(no_ignore) to control gitignore filtering

Harness-facing search JSON contract (version 1): - tg search --index --json emits structured
  SearchResultJson - Fields: version, routing_backend, routing_reason, query, path, total_matches,
  matches[{file, line, text}] - Matches all agent-facing contract requirements

25 index tests (16 unit + 9 integration): - Invalidation: content change, file deletion, new file,
  size change - Format: version byte, bad magic, future version, truncated file - Rebuild: stale
  corpus produces correct new results - CLI: build, count, JSON contract, verbose, cache reuse,
  stale rebuild, corrupt recovery, case-insensitive

84 total Rust tests, 510 Python tests, all pass.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **index**: Preserve regex parity for richer literal patterns
  ([`a3ddadf`](https://github.com/oimiragieo/tensor-grep/commit/a3ddadfc91c9f725914072db197823d134fd3833))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **mcp**: Add trigram index search tool
  ([`97b53bc`](https://github.com/oimiragieo/tensor-grep/commit/97b53bc4e563c092ddebf8f35009e1f2daab69bd))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **mcp**: Expose native rewrite workflow tools
  ([`7e946a3`](https://github.com/oimiragieo/tensor-grep/commit/7e946a34dbb5d6cdb6fd701d4c91ce51b92d9f75))

Add MCP wrappers for native rewrite plan, apply, and diff flows so harness clients can drive AST
  rewrites with unified routing metadata and structured errors.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **rewrite**: Add batch AST rewrite config support
  ([`6956fc1`](https://github.com/oimiragieo/tensor-grep/commit/6956fc15085883cd17be6f356b00c663cd646b61))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **rewrite**: Add diff output, idempotence tests, and rewrite benchmarks
  ([`0069168`](https://github.com/oimiragieo/tensor-grep/commit/0069168f56bb808918223c3b23badc2ddd094d98))

Add --diff flag to tg run --rewrite for unified diff preview output. Implement emit_unified_hunks()
  with proper context, hunk merging, and correct line numbering for multi-edit files.

Add 6 verification tests: - Idempotence (pattern no longer matches after apply) - Surrounding code
  preservation - Replacement length change (shrink/grow across edits) - Rust language rewrite - CRLF
  newline preservation - CLI diff output correctness

Add rewrite benchmark harness (run_ast_rewrite_benchmarks.py): - tg plan-only: 605ms (dry-run,
  primary AI harness interface) - tg apply: 1.46s (plan + write 1000 files) - sg apply: 807ms (apply
  is faster due to single-pass design) - Plan-only path is faster than sg's combined apply

57 Rust tests, 510 Python tests, all pass.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **rewrite**: Add native AST rewrite substrate with plan/apply
  ([`a5dad70`](https://github.com/oimiragieo/tensor-grep/commit/a5dad70c8e03ffb79addf579411214ff029728ab))

Add RewritePlan, RewriteEdit, and OverlapRejection models to backend_ast.rs. Implement
  plan_rewrites() using ast-grep-core's NodeMatch::replace_by() for metavar-aware pattern
  substitution. Parallel file processing via rayon, deterministic edit ordering, non-overlapping
  edit validation with rejected-overlap reporting.

CLI: tg run --rewrite REPLACEMENT emits JSON patch plan (dry-run). tg run --rewrite REPLACEMENT
  --apply writes files.

11 contract tests covering: - Metavar substitution (Python, JavaScript) - Multi-match per file,
  multi-file - Apply correctness - Deterministic ordering - JSON serialization - CLI dry-run vs
  apply - No-match reporting

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **routing**: Unify smart search routing decisions
  ([`986e9e0`](https://github.com/oimiragieo/tensor-grep/commit/986e9e05649188dba6684c835f0c07786764bade))

Centralize search backend selection in a single smart router so CLI paths share the same priority
  order, calibration-aware GPU auto-routing, and fallback behavior.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **rust-core**: Add ast-grep embed dependencies
  ([`c49526d`](https://github.com/oimiragieo/tensor-grep/commit/c49526d0ffb5c311771af4a445c7e520a0c0b368))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **rust-core**: Add native ast-grep run backend
  ([`24d55d3`](https://github.com/oimiragieo/tensor-grep/commit/24d55d3ebeaa3d7bd76f4814719e2cd2475a5fce))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **rust-core**: Add search parity and cold-start gates
  ([`1dbdda6`](https://github.com/oimiragieo/tensor-grep/commit/1dbdda6e7b522590544acff70350c34e42f5bbc5))

Route CLI search invocations through ripgrep-compatible paths so the Rust control plane keeps
  benchmark parity while staying Python-free. Add recorded golden search fixtures, a Windows PR
  parity job, and a hyperfine-based cold-start gate tied to the stored benchmark baseline.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **search**: Add native grep crate search scaffold
  ([`5d9c929`](https://github.com/oimiragieo/tensor-grep/commit/5d9c9292ece206910a3bcfa29c10bb6fd2727f5f))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **search**: Add NDJSON streaming output for matches
  ([`a7f9543`](https://github.com/oimiragieo/tensor-grep/commit/a7f95436260dfa125999d1e5846d2efb27435733))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **search**: Chunk-parallelize large native file scans
  ([`8a96e6d`](https://github.com/oimiragieo/tensor-grep/commit/8a96e6defa16864791453e6c05bf19831c0d471b))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **search**: Route native CPU search in CLI
  ([`03bc00f`](https://github.com/oimiragieo/tensor-grep/commit/03bc00fdb39e8de769d95ad0e7c4a052bbc975aa))

Route --cpu/--force-cpu, JSON output, and rg-unavailable searches through the embedded native engine
  so routing metadata and fallback behavior stay consistent.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **search**: Stream native stdout output incrementally
  ([`52ce9a6`](https://github.com/oimiragieo/tensor-grep/commit/52ce9a6fae697a8b4a89b9a03ec8bdc7a7310bf0))

Route default and NDJSON native output through streaming sinks so matches flush before search
  completion while JSON stays aggregated.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **sidecar**: Add JSON stdio Python sidecar
  ([`6599078`](https://github.com/oimiragieo/tensor-grep/commit/65990786dacc5dc3dd2007ccc91419580f937470))

Replace embedded Python subcommand execution with a JSON-over-stdio sidecar so classify parity,
  large payload handling, and exit-code propagation are testable and reliable.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **validation**: Add cross-backend parity coverage
  ([`1a16b2a`](https://github.com/oimiragieo/tensor-grep/commit/1a16b2a783300b35147547e3ec4ef70c6c40dbff))

Lock JSON envelope parity across backends and surface consistent index search errors.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

### Performance Improvements

- Lock ast and rewrite benchmark gates
  ([`8b2057f`](https://github.com/oimiragieo/tensor-grep/commit/8b2057f944c12c9e12bdedc9afe0c1ed990eeb51))

- Restore ripgrep cold-path routing baseline
  ([`8aea342`](https://github.com/oimiragieo/tensor-grep/commit/8aea34233bc369e481bb74a460eb84dc98473536))

- **ast**: Add direct bootstrap path for workflow commands
  ([`25890e2`](https://github.com/oimiragieo/tensor-grep/commit/25890e2bfe6c278b9b26728f9ed17d14808a5951))

- **ast**: Add direct workflow and project scan fast paths
  ([`740dc83`](https://github.com/oimiragieo/tensor-grep/commit/740dc8313ad85936050becbec951a62faeb5e00e))

- **ast**: Add workflow benchmark and fix wrapper path
  ([`3fcf73c`](https://github.com/oimiragieo/tensor-grep/commit/3fcf73c1cdd4e8c3b54abc1e89bc01d86175b929))

- **ast**: Batch wrapper rule tests
  ([`c93ad1e`](https://github.com/oimiragieo/tensor-grep/commit/c93ad1e5595ef7794fd60d66fa344a5d62451725))

- **ast**: Batch wrapper run across files
  ([`4a41ece`](https://github.com/oimiragieo/tensor-grep/commit/4a41ece170c4739115dd787a0b1f028aeb30a513))

- **ast**: Batch wrapper scan per rule
  ([`6dc3414`](https://github.com/oimiragieo/tensor-grep/commit/6dc34146c7391ddc9ed1aee8fcb499755b8c7807))

- **ast**: Batch wrapper test cases once per rule
  ([`5e8f70d`](https://github.com/oimiragieo/tensor-grep/commit/5e8f70d1894acd9a1131efe4446b90365487744c))

- **ast**: Bypass pipeline for workflow backend selection
  ([`050408c`](https://github.com/oimiragieo/tensor-grep/commit/050408cf8315bbe5a14cdf59eb9297da1fa0708f))

- **ast**: Bypass scanner for wrapper roots
  ([`e28c724`](https://github.com/oimiragieo/tensor-grep/commit/e28c724aa33e3e4b25629699cd06dd3f34258548))

- **ast**: Bypass temp-file writes for streamed rewrites
  ([`a1b6862`](https://github.com/oimiragieo/tensor-grep/commit/a1b686211d635371718d13059ae6e09023b34096))

Use direct overwrites for the one-shot plan+apply path so Windows rewrite apply spends time on AST
  rewriting instead of per-file temp-file creation and rename overhead.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **ast**: Cache tree-sitter queries and parsed source
  ([`7ff2e9c`](https://github.com/oimiragieo/tensor-grep/commit/7ff2e9c4f6c94331e05862124269d019809fcbf2))

- **ast**: Fuse rewrite apply file IO
  ([`ddb1dd4`](https://github.com/oimiragieo/tensor-grep/commit/ddb1dd45a882d6583b1a2e83402cac882fbd3712))

Reuse planned rewrite sources during fused apply, stream per-file writes, and drop per-file fsync so
  Windows rewrite apply spends less time on redundant I/O while preserving atomic temp-file
  replacement.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **ast**: Group wrapper rule tests by pattern
  ([`f57c256`](https://github.com/oimiragieo/tensor-grep/commit/f57c256350855586f81a191619f6fc62d73a56ea))

- **ast**: Group wrapper test batches by pattern
  ([`4cbf623`](https://github.com/oimiragieo/tensor-grep/commit/4cbf6232df5a8b61eb5acb02e9f98a9506e02b14))

- **ast**: Keep node indexes hot in memory
  ([`7d47e46`](https://github.com/oimiragieo/tensor-grep/commit/7d47e465b7447de34472114629024ca5d767c922))

- **ast**: Parallelize AST search with rayon and switch to ignore crate
  ([`880ce04`](https://github.com/oimiragieo/tensor-grep/commit/880ce045c5ee8368656a108bd6dcb3190934f00d))

Parallelize per-file AST pattern matching using rayon::par_iter and replace walkdir with the ignore
  crate for gitignore-aware file walking. Use fs::read + from_utf8 instead of read_to_string (avoids
  double alloc). Defer line_starts computation until first match per file. Remove unnecessary file
  sort from collection phase.

Before: tg run 929ms vs sg 311ms (2.98x slower)

After: tg run 325ms vs sg 444ms (1.37x faster than sg)

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **ast**: Persist cached results across runs
  ([`055503b`](https://github.com/oimiragieo/tensor-grep/commit/055503b7a47ad0b6f684df6c4149fcadc7ffafe3))

- **ast**: Persist node type index for native queries
  ([`61600a8`](https://github.com/oimiragieo/tensor-grep/commit/61600a815b96c61744ecf421b44d8a4d92dda314))

- **ast**: Prefer native backend for repeated workflows
  ([`88f4e8a`](https://github.com/oimiragieo/tensor-grep/commit/88f4e8a3f47eedabf41782e0aa00dfbf5addee1a))

- **ast**: Reuse workflow backend selection cache
  ([`b46bb01`](https://github.com/oimiragieo/tensor-grep/commit/b46bb0155f10ae04d3c58dce4bd6c52438baa467))

- **ast**: Share in-memory caches across instances
  ([`38bf625`](https://github.com/oimiragieo/tensor-grep/commit/38bf62526d343b384227d344139985de399b8483))

- **ast**: Trim CLI search match construction
  ([`a6ca2fa`](https://github.com/oimiragieo/tensor-grep/commit/a6ca2fa639e47fddc0c9294e1bc0363666d4e326))

Keep AST search results user-identical while avoiding rewrite-candidate allocation in the hot CLI
  path so the ast benchmark gate stays competitive.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **cli**: Bootstrap rg fast path and fix benchmark gate
  ([`aea1042`](https://github.com/oimiragieo/tensor-grep/commit/aea104241f4dabcc846dc6172d9f066ace70fd00))

- **cpu**: Persist regex prefilter cache
  ([`84cf51e`](https://github.com/oimiragieo/tensor-grep/commit/84cf51ee33d14fcc625ca832a94f7b9f7d3480a2))

- **cpu**: Prefilter repeated regex fallback
  ([`922803c`](https://github.com/oimiragieo/tensor-grep/commit/922803cd154e5da2b192e539117d45db4672bf2e))

- **rewrite**: Single-pass apply, formalized JSON contract, phase benchmarks
  ([`5d016a5`](https://github.com/oimiragieo/tensor-grep/commit/5d016a56251d228ac50776e2674b9963121a10fb))

Optimize --apply path: plan_and_apply() reads each file once, computes edits, builds rewritten
  content, and writes in parallel via rayon. Eliminates the second file-read pass.

Before: tg apply 1.46s vs sg 0.81s (1.81x slower)

After: tg apply 0.74s vs sg 0.70s (parity)

Formalize RewritePlan JSON contract (version 1): - version: schema version for forward compat -
  total_files_scanned: number of files examined - total_edits: number of accepted edits - edit id:
  deterministic e{seq}:{filename}:{start}-{end} - Full provenance: pattern, replacement, metavar_env
  per edit

Phase benchmarks (1000 files, 50k LOC): - plan: 545ms (search + build plan, no IO) - diff: 628ms
  (plan + unified diff generation) - apply: 739ms (single-pass plan + parallel write)

19 rewrite contract tests, 59 total Rust tests, 510 Python tests.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **rust**: Mmap in-place replace mutations
  ([`f89e858`](https://github.com/oimiragieo/tensor-grep/commit/f89e85858c629a50f39d3f1f2b4bf5e74ffe1376))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **string**: Add repeated-query trigram index
  ([`00ce777`](https://github.com/oimiragieo/tensor-grep/commit/00ce7773ad3ec655109d30b6bf9911e83d61fe47))

### Testing

- Add user testing synthesis for ast-search-speed
  ([`9256881`](https://github.com/oimiragieo/tensor-grep/commit/9256881f224aadab38f7496bba1a9bb6cdcb2dc5))

- Compatibility and regression tests for hardened contracts
  ([`8fcc2d9`](https://github.com/oimiragieo/tensor-grep/commit/8fcc2d9da030d1132bb9301c32bef9da9de5325c))

Rewrite contract tests (4 new): - Combined JSON shape: --apply --verify --json emits single document
  with plan + verification fields, valid JSON parse - Tampered file detection: verify catches
  post-apply file modification - Multi-edit length change verification: byte offsets track correctly
  across shrinking/growing edits in same file - Overlap rejection write safety: plan_and_apply only
  writes validated edits

Index routing regression tests (3 new): - Short pattern (<3 chars) falls through to rg, not index -
  Invert match (-v) falls through to rg, not index - Old/incompatible format triggers rebuild with
  diagnostic message

95 total Rust tests, 510 Python tests, all pass.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- Make sidecar rust tests self-contained
  ([`b04c7b2`](https://github.com/oimiragieo/tensor-grep/commit/b04c7b2dcd66153553abc42d02642e9bcd04cc39))

- Relax rust sidecar ci assumptions
  ([`d0e66af`](https://github.com/oimiragieo/tensor-grep/commit/d0e66afe863ae0d39d32097abc28ef3e790a8e20))

- Skip generated benchmark artifacts in clean checkout
  ([`3326b38`](https://github.com/oimiragieo/tensor-grep/commit/3326b3871759b75429158351d8a3f602e1265a5a))

- Stabilize ci ripgrep assumptions
  ([`6c508c9`](https://github.com/oimiragieo/tensor-grep/commit/6c508c97279a5911b35e89acd654b941f530505b))

- Stabilize cross-platform help and runtime path checks
  ([`a7e97f8`](https://github.com/oimiragieo/tensor-grep/commit/a7e97f808a6a571445268ae28f5f214c70aa7a40))

- Stabilize cross-platform schema and cudf expectations
  ([`2f57f14`](https://github.com/oimiragieo/tensor-grep/commit/2f57f14b6f268f603d722a6a39be03409aafc1e2))

- Stabilize sidecar timing on windows
  ([`6d4fead`](https://github.com/oimiragieo/tensor-grep/commit/6d4fead7caaacc5847ad70ed309cd58ba34105b4))

- Update synthesis for ast-search-speed with failed VAL-CROSS-001
  ([`2127046`](https://github.com/oimiragieo/tensor-grep/commit/21270460f989d5035920cbc4a819b66ae93d6c4d))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- Update synthesis for ast-search-speed with passed VAL-CROSS-001
  ([`95411c7`](https://github.com/oimiragieo/tensor-grep/commit/95411c7d4a95288082c141d8308d1fb4ea5f6fcb))

- **bundle**: Require exact validation commands
  ([`abbfe53`](https://github.com/oimiragieo/tensor-grep/commit/abbfe530ec96536917b397c4d9a7596545bf355c))

- **bundle**: Require install smoke commands
  ([`95b09dc`](https://github.com/oimiragieo/tensor-grep/commit/95b09dc470c19ce06236cf3adbbad837cea8b6fd))

- **bundle**: Require publish branch and git add commands
  ([`7d800f6`](https://github.com/oimiragieo/tensor-grep/commit/7d800f68d5f9b3624e02536047b6de2d18b5632d))

- **ci**: Require dist parity check in publish-pypi
  ([`8ce76bd`](https://github.com/oimiragieo/tensor-grep/commit/8ce76bda93807c40bfc7223766f0dc1596d07d63))

- **ci**: Require dist parity in publish-success-gate
  ([`639efd0`](https://github.com/oimiragieo/tensor-grep/commit/639efd00311d2e7d7e6f21584ca60b353585509e))

- **ci**: Require validate-pypi-artifacts step commands
  ([`0d3cf9a`](https://github.com/oimiragieo/tensor-grep/commit/0d3cf9a51b7af57b6f5b50d590c181a8d2b9c2cd))

- **ci**: Require validate-pypi-artifacts step flags
  ([`2a3a9b6`](https://github.com/oimiragieo/tensor-grep/commit/2a3a9b675a654b44c1cd2ac812103390cce8202d))

- **cudf**: Guard windows-only isolation coverage
  ([`393a8f3`](https://github.com/oimiragieo/tensor-grep/commit/393a8f33b47eb8c3bc8aaf3429b911ee5f0e180a))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **cybert**: Cover Triton timeout edge cases
  ([`b6cc054`](https://github.com/oimiragieo/tensor-grep/commit/b6cc054ccf5036854881df74373ffdac33a0b4d7))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **docs**: Require exact package-manager validation commands
  ([`3121e32`](https://github.com/oimiragieo/tensor-grep/commit/3121e32eee80cea63db77016cc816c63871d3eec))

- **gpu**: Lock collapsed cudf plan through pipeline
  ([`dfe60a7`](https://github.com/oimiragieo/tensor-grep/commit/dfe60a7a371bd97296f2b924302a62097e62cd7e))

- **gpu**: Lock torch multi-gpu routing metadata
  ([`a4dfc71`](https://github.com/oimiragieo/tensor-grep/commit/a4dfc71124f3c37f2b9d504daa3c855d5b165e34))

- **gpu**: Lock torch regex cpu fallback through pipeline
  ([`a8ff190`](https://github.com/oimiragieo/tensor-grep/commit/a8ff190cf3c6e580bd838f7f800708427c236a2e))

- **gpu**: Prefer runtime single-worker metadata in stats
  ([`b484a45`](https://github.com/oimiragieo/tensor-grep/commit/b484a456f90bfa1585f082a5fad8f17d3a8a9c60))

- **gpu**: Prefer runtime single-worker metadata in surfaces
  ([`c84e35b`](https://github.com/oimiragieo/tensor-grep/commit/c84e35b8b0e12984ef280e72a8e74520fa5fe0dd))

- **gpu**: Validate deferred correctness parity
  ([`770d970`](https://github.com/oimiragieo/tensor-grep/commit/770d970b15b1a7c330f6336bfd11c08b459cac00))

Record the live RTX 4070 validation path for VAL-GPU-006 so future workers can rerun GPU-vs-CPU/rg
  parity checks without relying only on the historical benchmark artifact.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **harness**: Lock docs examples to the v1 JSON schema
  ([`551db74`](https://github.com/oimiragieo/tensor-grep/commit/551db74491b4fe60a3e40d3642f8dae57d22640f))

Add a Rust integration test that parses every committed docs/examples JSON artifact, asserts the
  shared envelope fields, and validates each shape-specific payload so contract drift is caught
  immediately.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **index**: Lock 10k scaling benchmark validation
  ([`842944f`](https://github.com/oimiragieo/tensor-grep/commit/842944f8d1634b6a58dffc9e71f8f6fd71430e32))

Require 10k+ scale coverage, gate build time, and record indexed-vs-plain query parity so index
  scaling validation proves correctness as well as timing.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **pipeline**: Cover strict fallback contract paths
  ([`781d4dc`](https://github.com/oimiragieo/tensor-grep/commit/781d4dca9dddaa659494afb51a4755ee98177301))

Protect the explicit GPU and AST routing contracts with regression tests for torch import failures
  and fully unavailable AST backends.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **release**: Require binary artifact validation flags
  ([`4ba35b3`](https://github.com/oimiragieo/tensor-grep/commit/4ba35b336f3f2a75b83db1f894e024378836357f))

- **release**: Require binary smoke artifacts dir flag
  ([`c2e4a47`](https://github.com/oimiragieo/tensor-grep/commit/c2e4a47cb7afe8cdbac401d81cf1117f0373bae6))

- **release**: Require binary smoke verify version flag
  ([`c843792`](https://github.com/oimiragieo/tensor-grep/commit/c8437920900b0618cb008fdc84a7beb7faea194b))

- **release**: Require build-binaries install commands
  ([`19f703e`](https://github.com/oimiragieo/tensor-grep/commit/19f703ed2e2d504a7bd00aaf886d17b285824517))

- **release**: Require build-binaries rename commands
  ([`cf58439`](https://github.com/oimiragieo/tensor-grep/commit/cf584391232c70442f612c4dce7596ed6be7c335))

- **release**: Require build-binaries setup contract
  ([`750b0e5`](https://github.com/oimiragieo/tensor-grep/commit/750b0e5dac0d7f1ed8b63b0f0ebd6de9a3dda04a))

- **release**: Require build-binaries smoke commands
  ([`f933578`](https://github.com/oimiragieo/tensor-grep/commit/f933578ebf52c2ffe387db54a50e35bad3ae6d0f))

- **release**: Require build-binaries step contracts
  ([`010e8df`](https://github.com/oimiragieo/tensor-grep/commit/010e8df5775e678bb7db439ddf677d6fc419fdce))

- **release**: Require create-release artifact steps
  ([`b556f83`](https://github.com/oimiragieo/tensor-grep/commit/b556f83e2f1cffd77c66dad0ab8df29a7c80c28a))

- **release**: Require create-release download contract
  ([`269828a`](https://github.com/oimiragieo/tensor-grep/commit/269828acc50c828a8f82617445e1b080cd364868))

- **release**: Require create-release setup contract
  ([`203046a`](https://github.com/oimiragieo/tensor-grep/commit/203046a2aefbb14df8419227101fca79a7e34e4f))

- **release**: Require github release asset contract
  ([`a9509a6`](https://github.com/oimiragieo/tensor-grep/commit/a9509a64e5c266f630669930f8571b82acf48040))

- **release**: Require npm prepublish commands
  ([`ac73d33`](https://github.com/oimiragieo/tensor-grep/commit/ac73d3325b8a9ccc81abc9305b7ef998cc7016bf))

- **release**: Require npm setup-node contract
  ([`9d2603c`](https://github.com/oimiragieo/tensor-grep/commit/9d2603cb9fe1736ef542aa4c91ea9b2d7f7a0bf1))

- **release**: Require preflight package-manager step commands
  ([`3e6d0e3`](https://github.com/oimiragieo/tensor-grep/commit/3e6d0e3c6a95ce2f73961b60da5e2fa98c59e052))

- **release**: Require publish-docs checkout
  ([`82bbf88`](https://github.com/oimiragieo/tensor-grep/commit/82bbf88ec6c18b0e2ddbd0e99bde0e7140533cb9))

- **release**: Require publish-docs deploy commands
  ([`f199d85`](https://github.com/oimiragieo/tensor-grep/commit/f199d85e64800df45091b6a4dead6707827c3bb8))

- **release**: Require publish-docs deploy entrypoint
  ([`ec510a9`](https://github.com/oimiragieo/tensor-grep/commit/ec510a9049523afdca429c48362ee787ecfc2677))

- **release**: Require publish-docs force deploy
  ([`cf0b177`](https://github.com/oimiragieo/tensor-grep/commit/cf0b17742e79df0d02a193927969ac2baac9a124))

- **release**: Require publish-docs pip entrypoint
  ([`d39249d`](https://github.com/oimiragieo/tensor-grep/commit/d39249d471d3e0628282abe999661f2cae534369))

- **release**: Require publish-docs python setup
  ([`ac553d6`](https://github.com/oimiragieo/tensor-grep/commit/ac553d6ea05350947d339d23ffe43863fd6da603))

- **release**: Require publish-npm auth env
  ([`36edebe`](https://github.com/oimiragieo/tensor-grep/commit/36edebebc0380c6d219f54df6c3715edadfb1101))

- **release**: Require publish-npm checkout
  ([`838f127`](https://github.com/oimiragieo/tensor-grep/commit/838f127461efd957e8d1b95a7f270ce62e2158b0))

- **release**: Require publish-npm node version
  ([`da3a11a`](https://github.com/oimiragieo/tensor-grep/commit/da3a11ad4cebde4a410f1f9ab37173057d9c6d21))

- **release**: Require publish-npm parity entrypoint
  ([`74a6df9`](https://github.com/oimiragieo/tensor-grep/commit/74a6df96d8fdfca9960523b1686c7c682e5690d2))

- **release**: Require publish-npm uv setup
  ([`d4e0ca6`](https://github.com/oimiragieo/tensor-grep/commit/d4e0ca6cd74a71151029e75320f7993453536b47))

- **release**: Require publish-npm version gate
  ([`63e1218`](https://github.com/oimiragieo/tensor-grep/commit/63e121889ac0f7af42e081641c879091d730720d))

- **release**: Require publish-npm working directory
  ([`7cfa4a1`](https://github.com/oimiragieo/tensor-grep/commit/7cfa4a13fdf3169615ba02e25a4593b73157b72a))

- **release**: Require source-state bundle check command
  ([`842fcf2`](https://github.com/oimiragieo/tensor-grep/commit/842fcf204ddfe87950cac10d9309aaf567b10c55))

- **release**: Require success-gate confirmation
  ([`a398f39`](https://github.com/oimiragieo/tensor-grep/commit/a398f39cc279cbd74fc89f060e9c5554344707fb))

- **release**: Require success-gate parity script
  ([`6a33f63`](https://github.com/oimiragieo/tensor-grep/commit/6a33f634ef33a1eed4cd22ad4341449e3713a291))

- **release**: Require success-gate python entrypoint
  ([`89cf9aa`](https://github.com/oimiragieo/tensor-grep/commit/89cf9aad2e5fd698b7d1cdbf84e3b41b3a921f13))

- **release**: Require success-gate setup
  ([`77a963f`](https://github.com/oimiragieo/tensor-grep/commit/77a963fa43e6d9d0bdc11fe3a469ae383c20490c))

- **release**: Require tag parity setup actions
  ([`094e221`](https://github.com/oimiragieo/tensor-grep/commit/094e2215eb399ca1242f3fb1b8293d66068b5e03))

- **release**: Require tag parity setup contract
  ([`412b13a`](https://github.com/oimiragieo/tensor-grep/commit/412b13ae22a9172e2e5b992c4ead137ca3b0dfb6))

- **release**: Require verify-assets python entrypoint
  ([`9aaee76`](https://github.com/oimiragieo/tensor-grep/commit/9aaee761659dc4c9129db2da8711c822c93af67a))

- **release**: Require verify-release-assets checkout
  ([`1a970ea`](https://github.com/oimiragieo/tensor-grep/commit/1a970eaae369839dc69366d18a0b22f108e319bf))

- **release**: Validate built artifact metadata parity
  ([`9f5b74f`](https://github.com/oimiragieo/tensor-grep/commit/9f5b74fc39ebe84f2e8f162594c82909c466d6c7))

- **user-testing**: Gpu-and-index-scaling milestone validation - 20/21 pass, 1 blocked
  ([`7aee0f7`](https://github.com/oimiragieo/tensor-grep/commit/7aee0f7d0a12ed326f272bf7159aec9ab71656b0))

GPU assertions: 7/8 passed (VAL-GPU-006 blocked: GPU Python backends unavailable) Index assertions:
  11/11 passed (compression, incremental, regex, scaling, compat) Cross-area assertions: 2/2 passed
  (no benchmark regression, magic bytes preserved)

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **user-testing**: Harness-api milestone validation - all 16 assertions passed
  ([`8fb7ae7`](https://github.com/oimiragieo/tensor-grep/commit/8fb7ae73499d50d2f878bcf4025cccbbb6171bff))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>


## v0.31.24 (2026-03-12)

### Performance Improvements

- **release**: Build binaries from bootstrap entrypoint
  ([`c274bee`](https://github.com/oimiragieo/tensor-grep/commit/c274bee1d92c1cd74fe10f5e6a08896cf7cca7d8))


## v0.31.23 (2026-03-12)

### Performance Improvements

- **cpu**: Iterate regex prefilter candidates directly
  ([`8492dd2`](https://github.com/oimiragieo/tensor-grep/commit/8492dd2b127f9eafe28394d90bbbc272cc7e92e8))


## v0.31.22 (2026-03-12)

### Performance Improvements

- **cli**: Remove argparse from text fast path
  ([`da2acf0`](https://github.com/oimiragieo/tensor-grep/commit/da2acf0dc785c7ff028645f1ce612ce37c7a5a07))


## v0.31.21 (2026-03-12)

### Bug Fixes

- **ci**: Stop depending on rust-toolchain action
  ([`17b31b9`](https://github.com/oimiragieo/tensor-grep/commit/17b31b994bafbf488a6e74c3aa7a8f69926f8e21))

### Performance Improvements

- **cli**: Add no-rg search fast path
  ([`f686d13`](https://github.com/oimiragieo/tensor-grep/commit/f686d136c4024e9aba88806faadeee7930fe9217))

- **cli**: Lazy import rg passthrough helpers
  ([`0066cc0`](https://github.com/oimiragieo/tensor-grep/commit/0066cc0f0bd83eb1b3777d16e1af2f856a81713f))

- **cli**: Lazy import text backends in fast path
  ([`bdc977f`](https://github.com/oimiragieo/tensor-grep/commit/bdc977fd848dc882ae30be4f825e215b34bbed24))

- **cli**: Skip rg probe when path is empty
  ([`305ae35`](https://github.com/oimiragieo/tensor-grep/commit/305ae357e7a8f75e7976c84b9cabb2d8fc03a4da))

- **string**: Preallocate compact index decode
  ([`bdd58a9`](https://github.com/oimiragieo/tensor-grep/commit/bdd58a92f9997f1cdacabb9aab2a532d41918861))


## v0.31.20 (2026-03-12)

### Performance Improvements

- **cpu**: Speed regex prefilter intersections
  ([`3087914`](https://github.com/oimiragieo/tensor-grep/commit/3087914c714df53b303cb28f8ebe5676a6b1f43a))


## v0.31.19 (2026-03-12)

### Bug Fixes

- **ci**: Bootstrap uv via pip in ci jobs
  ([`1800aa7`](https://github.com/oimiragieo/tensor-grep/commit/1800aa798b7692ac30be589d6c2c8c63d0501845))

### Performance Improvements

- **cpu**: Compact persistent regex index
  ([`b359aa6`](https://github.com/oimiragieo/tensor-grep/commit/b359aa6f29bdde432a5647cecdac9e5ec1d4d83e))

- **string**: Speed indexed trigram intersections
  ([`56ab7e4`](https://github.com/oimiragieo/tensor-grep/commit/56ab7e49b4dc3bcfa952d640c91d2e11196fb933))


## v0.31.18 (2026-03-12)

### Performance Improvements

- **ast**: Lazy import pipeline fallback
  ([`1f1ee56`](https://github.com/oimiragieo/tensor-grep/commit/1f1ee5696c7139f9494207587e395df2a50ade2c))

- **ast**: Skip native backend construction for wrapper rules
  ([`7d69006`](https://github.com/oimiragieo/tensor-grep/commit/7d69006fbcd4b6e2131d5c7e91e6c6a6a4ba8150))


## v0.31.17 (2026-03-12)

### Bug Fixes

- **ci**: Format ast workflow payload cache
  ([`4a60bbc`](https://github.com/oimiragieo/tensor-grep/commit/4a60bbcd1bfb6884053b4fd818ff5172de1fa88b))

### Performance Improvements

- **ast**: Batch wrapper rule tests through project scan
  ([`e1f0074`](https://github.com/oimiragieo/tensor-grep/commit/e1f00747dd053a27bd9dffc90d02de136a03edb1))

- **ast**: Preload test payloads per workflow run
  ([`656db72`](https://github.com/oimiragieo/tensor-grep/commit/656db727dffcf66139cec5a32e3aaefdbe845e21))


## v0.31.16 (2026-03-11)

### Bug Fixes

- **ast**: Normalize wrapper temp match paths
  ([`3428b96`](https://github.com/oimiragieo/tensor-grep/commit/3428b96ccf763c0c0ead53b5742d1096ce1e8c57))

- **ci**: Format ast workflow matcher
  ([`b3f272a`](https://github.com/oimiragieo/tensor-grep/commit/b3f272a5b421eac88ac18ff4527609efc3cb2365))

### Performance Improvements

- **ast**: Move wrapper test batches to system temp
  ([`4643e8f`](https://github.com/oimiragieo/tensor-grep/commit/4643e8f0d248bef3df15c6bde64c50ae980f1f99))


## v0.31.15 (2026-03-11)

### Performance Improvements

- **ast**: Cache wrapper binary resolution
  ([`ddf568a`](https://github.com/oimiragieo/tensor-grep/commit/ddf568ac51c78751fbd6596237d5aa50b747d902))


## v0.31.14 (2026-03-11)

### Bug Fixes

- **ci**: Apply ruff format to ast workflows
  ([`a0976c9`](https://github.com/oimiragieo/tensor-grep/commit/a0976c93b75f033d32df13e2ad76545f0a469999))

- **ci**: Format ast workflows file
  ([`49d6fd2`](https://github.com/oimiragieo/tensor-grep/commit/49d6fd22f9760d12a989433c506a843ab0200618))

- **ci**: Format ast workflows for ruff
  ([`37c4548`](https://github.com/oimiragieo/tensor-grep/commit/37c4548369408ab5d9bf291883b4ad77949f9b7e))

- **ci**: Remove ast workflow conflict markers
  ([`57c421b`](https://github.com/oimiragieo/tensor-grep/commit/57c421b17eca92ae1ad5d76eb91c722f7122311c))

### Performance Improvements

- **ast**: Add direct workflow and project scan fast paths
  ([`fb5151d`](https://github.com/oimiragieo/tensor-grep/commit/fb5151d60173cc7d71fe1e80b0e697b3dafb0a7d))

- **ast**: Defer scanner import in workflow path
  ([`756c660`](https://github.com/oimiragieo/tensor-grep/commit/756c660fee74937945a042ee58aa98f1b9db451f))

- **ast**: Reuse rule-linked test resolution
  ([`54f7ac0`](https://github.com/oimiragieo/tensor-grep/commit/54f7ac00774614d324fbfd41b38cc6290d8c5cfc))

- **ast**: Reuse scan backend selection
  ([`c032cd4`](https://github.com/oimiragieo/tensor-grep/commit/c032cd4522b12b1e478d800163c58905dc48e69f))


## v0.31.13 (2026-03-11)

### Bug Fixes

- **ast**: Support multiline wrapper patterns
  ([`bb684bf`](https://github.com/oimiragieo/tensor-grep/commit/bb684bfee50b1dbad14da8a3fe6293493066cc81))

- **ci**: Format cli main for ruff
  ([`8e44473`](https://github.com/oimiragieo/tensor-grep/commit/8e444730b00f7548b7236c85c2c004a2d5822c2a))

- **ci**: Format direct ast workflow bootstrap path
  ([`0209cc3`](https://github.com/oimiragieo/tensor-grep/commit/0209cc3b0a23f92f971e013d03a18928f42f615c))

- **ci**: Harden install retries and format ast workflow files
  ([`105ffc8`](https://github.com/oimiragieo/tensor-grep/commit/105ffc89b8dbabd8bcd02a4bae752bb59482055a))

- **ci**: Match ruff formatter output
  ([`8ce4f8a`](https://github.com/oimiragieo/tensor-grep/commit/8ce4f8a0a2074f243fc747b6c948bc519f46f824))

- **ci**: Normalize linux formatter shapes
  ([`21c57e9`](https://github.com/oimiragieo/tensor-grep/commit/21c57e9c52cbd082b249ddc8064960a3c3dbec3f))

### Performance Improvements

- **ast**: Add direct bootstrap path for workflow commands
  ([`e7d2b24`](https://github.com/oimiragieo/tensor-grep/commit/e7d2b24b9c2fb2596ce2fd3bde27f257b73810f9))

- **ast**: Batch wrapper rule tests
  ([`2e08e23`](https://github.com/oimiragieo/tensor-grep/commit/2e08e23444129a5c4772a3d454bca4ffe855c85d))

- **ast**: Batch wrapper test cases once per rule
  ([`227b679`](https://github.com/oimiragieo/tensor-grep/commit/227b679ae194168cbb22113d7b4f9f6443d4dcd3))

- **ast**: Group wrapper rule tests by pattern
  ([`2c1fe42`](https://github.com/oimiragieo/tensor-grep/commit/2c1fe42736ce84b52fb2ea4c3fa51bc0ad40fda8))

- **ast**: Group wrapper test batches by pattern
  ([`b37d857`](https://github.com/oimiragieo/tensor-grep/commit/b37d8576d1dc29576a0cee44791ae2cb4dbdfbc5))

- **ast**: Route run through direct workflow path
  ([`5b57673`](https://github.com/oimiragieo/tensor-grep/commit/5b57673291ed9a8ca4d581dd79f386bcdb163036))


## v0.31.12 (2026-03-11)

### Performance Improvements

- **ast**: Bypass scanner for wrapper roots
  ([`c6949ae`](https://github.com/oimiragieo/tensor-grep/commit/c6949aef9ee61a5f92f20df74a9aa1b06cbd369a))


## v0.31.11 (2026-03-11)

### Performance Improvements

- **ast**: Batch wrapper run across files
  ([`2392205`](https://github.com/oimiragieo/tensor-grep/commit/2392205bbf50e99b22d071e829660b8c5eaf668f))

- **ast**: Batch wrapper scan per rule
  ([`848af3b`](https://github.com/oimiragieo/tensor-grep/commit/848af3bf17f426ed88830155775c183d189c0911))


## v0.31.10 (2026-03-11)

### Bug Fixes

- **ast**: Route native backend only for native patterns
  ([`47d8b59`](https://github.com/oimiragieo/tensor-grep/commit/47d8b59d3f3bc0d43267abd3b7e644a9e7245c00))

- **ci**: Annotate ast backend selection helper
  ([`c6fd803`](https://github.com/oimiragieo/tensor-grep/commit/c6fd8031a42625ae601d262a1e2e5feb8e1d4507))

- **ci**: Apply ruff formatting to backend caches
  ([`9f0ee25`](https://github.com/oimiragieo/tensor-grep/commit/9f0ee253ed13f58006b0589689fe5c8a1068b000))

- **ci**: Apply ruff formatting to cpu prefilter
  ([`65c0a94`](https://github.com/oimiragieo/tensor-grep/commit/65c0a9498ee22de32ed53a83386e3765dc3a46c4))

- **ci**: Format ast workflow cache helper
  ([`eb7b635`](https://github.com/oimiragieo/tensor-grep/commit/eb7b6356fc2bf5748d3a7c02a2e5615fe2808612))

- **ci**: Satisfy ruff type annotation style
  ([`cbbc44b`](https://github.com/oimiragieo/tensor-grep/commit/cbbc44b30a505c64d97fc9d1613efee19c5daf40))

### Performance Improvements

- **ast**: Add workflow benchmark and fix wrapper path
  ([`3976826`](https://github.com/oimiragieo/tensor-grep/commit/39768263708380b262e720e2d7e21837d15a7b43))

- **ast**: Bypass pipeline for workflow backend selection
  ([`1a2f04b`](https://github.com/oimiragieo/tensor-grep/commit/1a2f04b3f9403b1ff2cf2991a28457239df027f9))

- **ast**: Keep node indexes hot in memory
  ([`8b8e1ee`](https://github.com/oimiragieo/tensor-grep/commit/8b8e1ee66e12babcd1567487a099d7f82413261d))

- **ast**: Persist node type index for native queries
  ([`6d749a7`](https://github.com/oimiragieo/tensor-grep/commit/6d749a7406901a285ce995c407015f899812b306))

- **ast**: Reuse workflow backend selection cache
  ([`9c2d873`](https://github.com/oimiragieo/tensor-grep/commit/9c2d873efc0d7365a7d7f8fc4744170957f7a5e9))

- **ast**: Share in-memory caches across instances
  ([`69fba09`](https://github.com/oimiragieo/tensor-grep/commit/69fba0973edd52032b52426e5b10aa5a65378f7b))

- **cpu**: Persist regex prefilter cache
  ([`1177fad`](https://github.com/oimiragieo/tensor-grep/commit/1177fad2d13e1336525c360ae5b99a7a3517236b))

- **cpu**: Prefilter repeated regex fallback
  ([`c042acd`](https://github.com/oimiragieo/tensor-grep/commit/c042acd3ade68952a937471e20b88c0437f350d1))

- **string**: Add repeated-query trigram index
  ([`53c88fa`](https://github.com/oimiragieo/tensor-grep/commit/53c88fa9af459f979daf73caa5cce944a01ddbbc))


## v0.31.9 (2026-03-10)

### Performance Improvements

- **ast**: Prefer native backend for repeated workflows
  ([`1226560`](https://github.com/oimiragieo/tensor-grep/commit/122656063c3333e9fa2a67c4d99cfc028f7daa3a))


## v0.31.8 (2026-03-10)

### Performance Improvements

- **ast**: Persist cached results across runs
  ([`3731823`](https://github.com/oimiragieo/tensor-grep/commit/37318235756164445b55595fdaa397ba2b9ae16a))


## v0.31.7 (2026-03-10)

### Performance Improvements

- **ast**: Cache tree-sitter queries and parsed source
  ([`640491c`](https://github.com/oimiragieo/tensor-grep/commit/640491c6484e2ebbe6fcf4a5ed3e1605e8670fdf))


## v0.31.6 (2026-03-10)

### Documentation

- **benchmark**: Refresh latest benchmark and parity contracts
  ([`d74a94e`](https://github.com/oimiragieo/tensor-grep/commit/d74a94e5436cb793456e215905db6ac8b6a651eb))

### Performance Improvements

- **cli**: Bootstrap rg fast path and fix benchmark gate
  ([`3aed746`](https://github.com/oimiragieo/tensor-grep/commit/3aed74630c9fd977750865f146f6ea3d30a2e435))

### Testing

- **ci**: Require validate-pypi-artifacts step commands
  ([`52da731`](https://github.com/oimiragieo/tensor-grep/commit/52da7313b986ffcdf5b7b79ddee54b867315794a))

- **release**: Require build-binaries install commands
  ([`733ace1`](https://github.com/oimiragieo/tensor-grep/commit/733ace1e843e69d8d3ac7f31ce6d1fca468bdd1a))

- **release**: Require build-binaries rename commands
  ([`4a51110`](https://github.com/oimiragieo/tensor-grep/commit/4a5111067692e3cf0427ed7b68b9221b993c815c))

- **release**: Require build-binaries setup contract
  ([`f1e3b17`](https://github.com/oimiragieo/tensor-grep/commit/f1e3b173576b0814c7c56f9f348d191f68ded5c7))

- **release**: Require build-binaries smoke commands
  ([`73b47b3`](https://github.com/oimiragieo/tensor-grep/commit/73b47b3071205b889f73590668fef914609289af))

- **release**: Require build-binaries step contracts
  ([`2aea343`](https://github.com/oimiragieo/tensor-grep/commit/2aea343621f31876d030a5336f2cf141e78bcd22))

- **release**: Require create-release artifact steps
  ([`1828a1a`](https://github.com/oimiragieo/tensor-grep/commit/1828a1a7a39c4272fe4a33a21220ee7dc63bf0f3))

- **release**: Require create-release download contract
  ([`41137eb`](https://github.com/oimiragieo/tensor-grep/commit/41137ebc637494f098ac4108d266f9c5984692d8))

- **release**: Require create-release setup contract
  ([`7dff7b3`](https://github.com/oimiragieo/tensor-grep/commit/7dff7b3aab2b260d67c6cb883f605c5b725b9c38))

- **release**: Require github release asset contract
  ([`058458f`](https://github.com/oimiragieo/tensor-grep/commit/058458f33a9ed77e9dad28609971898f2153a330))

- **release**: Require npm prepublish commands
  ([`ad547fb`](https://github.com/oimiragieo/tensor-grep/commit/ad547fb4beade692671c5e06a92b7c8e3cb498b4))

- **release**: Require npm setup-node contract
  ([`aed8ae6`](https://github.com/oimiragieo/tensor-grep/commit/aed8ae6a58dd49701e2348e743fa32191321ca78))

- **release**: Require preflight package-manager step commands
  ([`c6c17f1`](https://github.com/oimiragieo/tensor-grep/commit/c6c17f181c0a1603481a7c358bb00a2acbe94974))

- **release**: Require publish-docs checkout
  ([`1cb3342`](https://github.com/oimiragieo/tensor-grep/commit/1cb334203706143f157e25f1674d63551a30242e))

- **release**: Require publish-docs deploy commands
  ([`8c1375f`](https://github.com/oimiragieo/tensor-grep/commit/8c1375f136da4546a8b3a69a66705bb48d072661))

- **release**: Require publish-docs deploy entrypoint
  ([`79f0bdd`](https://github.com/oimiragieo/tensor-grep/commit/79f0bdd4082a9272ef4beb3afc41cb2f13c9ce4b))

- **release**: Require publish-docs force deploy
  ([`f4f1c4e`](https://github.com/oimiragieo/tensor-grep/commit/f4f1c4eafa92f295fa8aae59a3a54d4158c2d9f4))

- **release**: Require publish-docs pip entrypoint
  ([`3ebc5b1`](https://github.com/oimiragieo/tensor-grep/commit/3ebc5b13ed68d299e8e12aa5ec81768f77f8a9f1))

- **release**: Require publish-docs python setup
  ([`4531b32`](https://github.com/oimiragieo/tensor-grep/commit/4531b32ea3242961336909c856410b96de0abce0))

- **release**: Require publish-npm auth env
  ([`be0cc7f`](https://github.com/oimiragieo/tensor-grep/commit/be0cc7f179f1ce4c7eaacde9325c197dc256ed1d))

- **release**: Require publish-npm checkout
  ([`94c0075`](https://github.com/oimiragieo/tensor-grep/commit/94c0075fe1a19d10ea55fdce0dc7e04149e64fd2))

- **release**: Require publish-npm node version
  ([`2922540`](https://github.com/oimiragieo/tensor-grep/commit/2922540a6a1912a6d3735c21f62a703835fb64e5))

- **release**: Require publish-npm parity entrypoint
  ([`df2f33f`](https://github.com/oimiragieo/tensor-grep/commit/df2f33f5f8e2bde8aab79728e66f7d23586be3bc))

- **release**: Require publish-npm uv setup
  ([`23bd6c4`](https://github.com/oimiragieo/tensor-grep/commit/23bd6c4cd52b46cd1bfc8d860852e38dc127ca62))

- **release**: Require publish-npm version gate
  ([`1cd74b8`](https://github.com/oimiragieo/tensor-grep/commit/1cd74b8329e59970c9b9eb3b7a2c6e09ffc34653))

- **release**: Require publish-npm working directory
  ([`52d3060`](https://github.com/oimiragieo/tensor-grep/commit/52d3060d89ce3681b4df8ce8e616008ed9841f1e))

- **release**: Require source-state bundle check command
  ([`9d103d5`](https://github.com/oimiragieo/tensor-grep/commit/9d103d590c9305ccb7191fdd45a66b9113812869))

- **release**: Require success-gate confirmation
  ([`440a7a3`](https://github.com/oimiragieo/tensor-grep/commit/440a7a39f97d5c1616c9bf2713e3693d60aef99d))

- **release**: Require success-gate parity script
  ([`adf940e`](https://github.com/oimiragieo/tensor-grep/commit/adf940eaf6c7f764114eaa3b3e0e0052df118f15))

- **release**: Require success-gate python entrypoint
  ([`d774eb4`](https://github.com/oimiragieo/tensor-grep/commit/d774eb4f1cbd06141a6ec10131e095bb5227d988))

- **release**: Require success-gate setup
  ([`af4579e`](https://github.com/oimiragieo/tensor-grep/commit/af4579e474776a100901bf4080a25a3107415c53))

- **release**: Require tag parity setup actions
  ([`2562577`](https://github.com/oimiragieo/tensor-grep/commit/2562577ca1e867676189bd3f4b651b5723f4f772))

- **release**: Require tag parity setup contract
  ([`baa044b`](https://github.com/oimiragieo/tensor-grep/commit/baa044be3479578f209ebbafeae270e48ea1030c))

- **release**: Require verify-assets python entrypoint
  ([`6d07efb`](https://github.com/oimiragieo/tensor-grep/commit/6d07efb4903fbd1299b284f55f5a5d7809c9063d))

- **release**: Require verify-release-assets checkout
  ([`fd9176d`](https://github.com/oimiragieo/tensor-grep/commit/fd9176df519431beca45c125e0e0a468c6f2291b))


## v0.31.5 (2026-03-09)

### Bug Fixes

- **ast**: Accept ast-grep wrapper in run command
  ([`2f93f8d`](https://github.com/oimiragieo/tensor-grep/commit/2f93f8dc811949007ac8dc46e356b50f3ea6d50d))

- **ast**: Honor count-only file contract in workflows
  ([`391c3e5`](https://github.com/oimiragieo/tensor-grep/commit/391c3e5e1180b066ced48c4349d595fa095e39ce))

- **benchmark**: Skip cybert when triton is unavailable
  ([`89dcfc8`](https://github.com/oimiragieo/tensor-grep/commit/89dcfc8bf140d5be7eb6d5c5e3691ad30df176d8))

- **cli**: Report matched files for count-only stats
  ([`bf338fe`](https://github.com/oimiragieo/tensor-grep/commit/bf338fe9a0ed2570ef797d2479f401c2cb50db43))

- **cli**: Surface gpu chunk plans without device ids
  ([`7cc2aa5`](https://github.com/oimiragieo/tensor-grep/commit/7cc2aa50920cf4796eb92c3979d3f96e73cc3fed))

- **cli**: Track matched files for count-only results
  ([`3dbd816`](https://github.com/oimiragieo/tensor-grep/commit/3dbd816e31b59ef2186f60c85e2f456b0ce53bc6))

- **cpu**: Report files for count-only python fallback
  ([`ceae4cb`](https://github.com/oimiragieo/tensor-grep/commit/ceae4cbc4a158ab586219cad9742bb4c238497ee))

- **cudf**: Count matched files correctly
  ([`f06d768`](https://github.com/oimiragieo/tensor-grep/commit/f06d7687f7e09ea6aa43c4a75111396aef01a281))

- **cudf**: Report single-worker plan accurately
  ([`eb949df`](https://github.com/oimiragieo/tensor-grep/commit/eb949dfc3e48d48a2fb48b447fc2c93a1876aca6))

- **deps**: Narrow triton nlp extra to http client
  ([`31b768b`](https://github.com/oimiragieo/tensor-grep/commit/31b768b0506c7677e256347efc4e291c082aa0ac))

- **json**: Expose matched file metadata
  ([`b7d32a4`](https://github.com/oimiragieo/tensor-grep/commit/b7d32a4fbb891940c1873339c99713bbc302c0e9))

- **json**: Persist aggregated matched file metadata
  ([`e5ec060`](https://github.com/oimiragieo/tensor-grep/commit/e5ec060a7e16eaf63c71d24ed44121f942b1db57))

- **mcp**: Count matched files for count-only results
  ([`cfa923d`](https://github.com/oimiragieo/tensor-grep/commit/cfa923d80784c1e26da904466355797f1051c31a))

- **mcp**: Finalize aggregate file metadata
  ([`d34c49b`](https://github.com/oimiragieo/tensor-grep/commit/d34c49ba7ed0ddb0b1193c282581ec0bee2be2bb))

- **mcp**: Include routing in count responses
  ([`8f3d6c3`](https://github.com/oimiragieo/tensor-grep/commit/8f3d6c3cb92b35b0b6df9e24285d97958e184e9d))

- **mcp**: Summarize count-only file results
  ([`7653094`](https://github.com/oimiragieo/tensor-grep/commit/7653094d6bfb17fc54acd83c7accf82404d7fd8e))

- **rg**: Parse count output without json mode
  ([`638b5d8`](https://github.com/oimiragieo/tensor-grep/commit/638b5d8d0a97253e341084b0e5e736ac4f352dcc))

- **rg**: Preserve matched file paths for count modes
  ([`9d89389`](https://github.com/oimiragieo/tensor-grep/commit/9d8938997b61a60faa3f198b4200178993f8c417))

- **rg**: Preserve per-file counts for count output
  ([`a9c827c`](https://github.com/oimiragieo/tensor-grep/commit/a9c827c4f2d075d65d2ad6c93654c01d18d88066))

- **run**: Report actual ast backend mode
  ([`3765f0c`](https://github.com/oimiragieo/tensor-grep/commit/3765f0c3646807d9ebf06146696d481118cae6e2))

- **scan**: Report actual ast backend mode
  ([`0a71504`](https://github.com/oimiragieo/tensor-grep/commit/0a715049c140c3379c80490327ecb146f83f4f67))

- **test**: Report actual ast backend mode
  ([`3c8d7b5`](https://github.com/oimiragieo/tensor-grep/commit/3c8d7b53e293d35bfadeada276d7913936ccd5eb))

- **torch**: Preserve routing metadata on empty pattern
  ([`12bf1e6`](https://github.com/oimiragieo/tensor-grep/commit/12bf1e672728f771f9025cc4127c9609eb0170bc))

- **torch**: Resolve mypy no-redef in search path
  ([`91f4293`](https://github.com/oimiragieo/tensor-grep/commit/91f42939d6873cf98fba6ff9df7da39afad60fba))

- **version**: Derive fallback cli version from pyproject
  ([`2a09a53`](https://github.com/oimiragieo/tensor-grep/commit/2a09a5319376c910e5666570de27c782c1f0167b))

### Documentation

- **benchmark**: Lock local benchmark install contract
  ([`26f29ab`](https://github.com/oimiragieo/tensor-grep/commit/26f29ab150a79ec68b8ca650852b3a79c790a633))

- **benchmark**: Refresh results for 2026-03-09 run
  ([`d2e9a97`](https://github.com/oimiragieo/tensor-grep/commit/d2e9a97e5dd28132da036ef642e9c0eb12c932e6))

### Testing

- **bundle**: Require exact validation commands
  ([`980e127`](https://github.com/oimiragieo/tensor-grep/commit/980e1272e1a76fd3246c54429a4244a4bd63bbdf))

- **bundle**: Require install smoke commands
  ([`ba6d780`](https://github.com/oimiragieo/tensor-grep/commit/ba6d780f6455536045836e023d4b58624571e7c5))

- **bundle**: Require publish branch and git add commands
  ([`809ccea`](https://github.com/oimiragieo/tensor-grep/commit/809ccea3fdd420325e3b1c8ee4790af5052cbdae))

- **ci**: Require dist parity check in publish-pypi
  ([`06af847`](https://github.com/oimiragieo/tensor-grep/commit/06af8470770fa4b3c84c66577032b75337f6e62c))

- **ci**: Require dist parity in publish-success-gate
  ([`4c34daa`](https://github.com/oimiragieo/tensor-grep/commit/4c34daadf6130ed14bb27025de27355d266aa3fa))

- **ci**: Require validate-pypi-artifacts step flags
  ([`b5a5731`](https://github.com/oimiragieo/tensor-grep/commit/b5a573106a9c6cfd3d24b0544c8583cb1743c627))

- **docs**: Require exact package-manager validation commands
  ([`4855a8f`](https://github.com/oimiragieo/tensor-grep/commit/4855a8f77f3f678a20e28b65cf57563914fe7627))

- **gpu**: Lock collapsed cudf plan through pipeline
  ([`f6a8a6b`](https://github.com/oimiragieo/tensor-grep/commit/f6a8a6b711316202ec2dca31d0b933fe2636f3dd))

- **gpu**: Lock torch regex cpu fallback through pipeline
  ([`ab06202`](https://github.com/oimiragieo/tensor-grep/commit/ab06202f7a0ac3ec1fab525d269956cb1ad86f86))

- **gpu**: Prefer runtime single-worker metadata in stats
  ([`0d1aac4`](https://github.com/oimiragieo/tensor-grep/commit/0d1aac492ce1320f17d608cfd9c1326d583e1f68))

- **gpu**: Prefer runtime single-worker metadata in surfaces
  ([`afb6c80`](https://github.com/oimiragieo/tensor-grep/commit/afb6c802725942081f214df2fbcfc9d9c9800b89))

- **release**: Require binary artifact validation flags
  ([`2bf3361`](https://github.com/oimiragieo/tensor-grep/commit/2bf33617a4436054fecc605d075c83f6dda5ad70))

- **release**: Require binary smoke artifacts dir flag
  ([`6c42095`](https://github.com/oimiragieo/tensor-grep/commit/6c42095dde259ce85e1570124df1155425b8f67a))

- **release**: Require binary smoke verify version flag
  ([`5f71b16`](https://github.com/oimiragieo/tensor-grep/commit/5f71b16128b727a18f14c493e97e42ec4182a786))

- **release**: Validate built artifact metadata parity
  ([`10d3088`](https://github.com/oimiragieo/tensor-grep/commit/10d30886eb1918b7d2d5cc88094a384f09102359))


## v0.31.4 (2026-03-08)

### Bug Fixes

- **routing**: Preserve backend identity on empty paths
  ([`8c8e7da`](https://github.com/oimiragieo/tensor-grep/commit/8c8e7da2d19d8ca8132a32fbd70797cefab84bfe))


## v0.31.3 (2026-03-08)

### Bug Fixes

- **cli**: Infer gpu worker metadata from selected routing
  ([`6f20fc6`](https://github.com/oimiragieo/tensor-grep/commit/6f20fc6e6d749db648d47ab8f49fc328324e0aa8))

### Testing

- **gpu**: Lock torch multi-gpu routing metadata
  ([`02a605a`](https://github.com/oimiragieo/tensor-grep/commit/02a605ab8aff7e5a7ecac7af80a2afbbaac40bcf))


## v0.31.2 (2026-03-08)

### Bug Fixes

- **inventory**: Honor authoritative empty gpu enumeration
  ([`91ed5b4`](https://github.com/oimiragieo/tensor-grep/commit/91ed5b493f72c9d5af58ed1ce690444789b70efd))

- **mcp**: Report runtime routing overrides
  ([`c4f9686`](https://github.com/oimiragieo/tensor-grep/commit/c4f9686c90822c91150c9c557b2263ae12f49432))

- **memory**: Treat empty gpu enumeration as authoritative
  ([`ad67866`](https://github.com/oimiragieo/tensor-grep/commit/ad67866bfd44dd17fe9e5b7aa1ba807a1dada351))

- **routing**: Emit runtime metadata for cpu fallbacks
  ([`5c4aae3`](https://github.com/oimiragieo/tensor-grep/commit/5c4aae3e3d73e47a766a82ad8d0d77243a3b02c6))

- **routing**: Stamp ast and string backends
  ([`b8297fd`](https://github.com/oimiragieo/tensor-grep/commit/b8297fd674776ab2555eca26c73363fff60e6e8c))

- **routing**: Stamp rg and rust backend metadata
  ([`c9d6aea`](https://github.com/oimiragieo/tensor-grep/commit/c9d6aea6e114a51aeca302691734f2cc081f0710))

- **torch**: Deduplicate duplicate gpu ids before fanout
  ([`3658891`](https://github.com/oimiragieo/tensor-grep/commit/365889160a0723186b2dccbf61ea8f1e5b14552c))

- **torch**: Fallback cleanly when gpu enumeration fails
  ([`b000373`](https://github.com/oimiragieo/tensor-grep/commit/b0003739f6f916b6a79d0aac7331eff9513d17aa))

- **torch**: Honor enumerated gpu ids in availability checks
  ([`f7f2599`](https://github.com/oimiragieo/tensor-grep/commit/f7f2599ccfe03e030b9ecb6d8c53ff583c3fb30d))

- **torch**: Require concrete gpu ids for routing
  ([`c75907f`](https://github.com/oimiragieo/tensor-grep/commit/c75907fa8d407890186abc0f6f3d3572d0b20a5c))

### Continuous Integration

- **gpu**: Add retry/backoff for cudf dependency install
  ([`755df5a`](https://github.com/oimiragieo/tensor-grep/commit/755df5a9e1c65d0b982070a22a7507e259f1c423))

- **pkg**: Smoke-test package-manager bundle contracts
  ([`e902ef7`](https://github.com/oimiragieo/tensor-grep/commit/e902ef7e16a5361e0f41573cec28b4dfa52d0f9c))

- **release**: Preflight package-manager bundle checks before build
  ([`a63e607`](https://github.com/oimiragieo/tensor-grep/commit/a63e60746eb6e4418178a9ce2d1f7ee6a0a48e8a))

- **release**: Smoke-test package-manager bundle in tag workflow
  ([`84cf28b`](https://github.com/oimiragieo/tensor-grep/commit/84cf28b5845bac5953fe7880ffbca124888385a3))

### Documentation

- **bench**: Refresh README and paper with latest benchmark pass
  ([`f7bf455`](https://github.com/oimiragieo/tensor-grep/commit/f7bf455bbf732ca444c169e1288ec934738d3712))

- **benchmark**: Refresh measured results on current line
  ([`7e00bc9`](https://github.com/oimiragieo/tensor-grep/commit/7e00bc90c447c292272c5a28c3f70499c2208826))

- **benchmark**: Refresh README and paper with 2026-03-08 results
  ([`da7164f`](https://github.com/oimiragieo/tensor-grep/commit/da7164f43a87b2b813bf1be3fbc8f63db7072126))

- **install**: Add explicit rollback smoke commands
  ([`ec2cac3`](https://github.com/oimiragieo/tensor-grep/commit/ec2cac33a381fdc4f7382a3923820fe8ebd6acb8))

- **install**: Require package-manager smoke commands
  ([`aea93bd`](https://github.com/oimiragieo/tensor-grep/commit/aea93bd6a667650a1728e99e8304ba7c4ef37924))

- **install**: Require tap install and asset verification commands
  ([`5306115`](https://github.com/oimiragieo/tensor-grep/commit/5306115bcc6a011d06bf7769cd85e3ee3618c185))

- **release**: Document bundle smoke-test gate and lock validator
  ([`8ff6a78`](https://github.com/oimiragieo/tensor-grep/commit/8ff6a781ed317496b272512686127b9bc7ed43f0))

- **runbook**: Standardize operator run listing command
  ([`8e3280a`](https://github.com/oimiragieo/tensor-grep/commit/8e3280a6149cf4828a0ca4f7926c4fc300844f74))

### Testing

- **ci**: Enforce publish-pypi parity step check and retry flags
  ([`687539c`](https://github.com/oimiragieo/tensor-grep/commit/687539ca5a2537a363f33353b0c0c3f3363ced88))

- **ci**: Enforce publish-success-gate pypi parity step flags
  ([`a47f64d`](https://github.com/oimiragieo/tensor-grep/commit/a47f64d920ac29e4e225e15739caef3a7650813c))

- **ci**: Enforce structural benchmark-regression baseline-auto steps
  ([`4ffa609`](https://github.com/oimiragieo/tensor-grep/commit/4ffa60982962f7bb8f1814874c9881b2294ecd6d))

- **ci**: Enforce structural gpu job retry and pytest steps
  ([`8a56fd3`](https://github.com/oimiragieo/tensor-grep/commit/8a56fd3ece65d00e6dc29dd9463e079c5f39f515))

- **ci**: Require parity identity flags in pypi gate steps
  ([`cf7b7aa`](https://github.com/oimiragieo/tensor-grep/commit/cf7b7aa4c33f8da73f18bb79964d66fd9a71f21f))

- **ci**: Require publish parity step presence in pypi and success gate
  ([`7af07e7`](https://github.com/oimiragieo/tensor-grep/commit/7af07e77e46b9a7cc9131187c5f9af0d39ab7e87))

- **docs**: Require bundle smoke-test command in publish runbook
  ([`bd43aea`](https://github.com/oimiragieo/tensor-grep/commit/bd43aeaed8f0192875e955da7f5fbb4924e73082))

- **docs**: Require install docs package-manager commands
  ([`dda7bf9`](https://github.com/oimiragieo/tensor-grep/commit/dda7bf978539cb00efccc71de2f97645ea5c2805))

- **docs**: Require release asset verification in runbook
  ([`db9992b`](https://github.com/oimiragieo/tensor-grep/commit/db9992ba3f2aa441d2be9e08850fdb4fbcfaf640))

- **pipeline**: Lock explicit multi-gpu torch fanout path
  ([`e02f2e8`](https://github.com/oimiragieo/tensor-grep/commit/e02f2e8a4175b14778c6da3a2b13465d618fbd60))

- **release**: Detect duplicate release assets and checksums
  ([`54a1473`](https://github.com/oimiragieo/tensor-grep/commit/54a1473a84af17dc1edfd2c9f4bd48d2eed78ded))

- **release**: Enforce create-release bundle step command contracts
  ([`9824305`](https://github.com/oimiragieo/tensor-grep/commit/98243053cbcc4ad54aa0e35b96d3d7ac655e41a3))

- **release**: Enforce create-release bundle verify and smoke steps
  ([`5d6b261`](https://github.com/oimiragieo/tensor-grep/commit/5d6b261dfe81cdc296a8fee96f5939b25ca6a45a))

- **release**: Enforce parity step flags in publish and release gate
  ([`db1c5a7`](https://github.com/oimiragieo/tensor-grep/commit/db1c5a7c3a22623e8b1f75b67cd47fadd79bfb9e))

- **release**: Enforce preflight bundle steps in validate-package-managers job
  ([`10570c3`](https://github.com/oimiragieo/tensor-grep/commit/10570c30c60c919f2e204222b4f6d371763728d8))

- **release**: Enforce validate-tag-version-parity step flags
  ([`5ca5c14`](https://github.com/oimiragieo/tensor-grep/commit/5ca5c147fdeabdd0a46eafece2cd9f480ba58001))

- **release**: Enforce verify-release-assets step contracts
  ([`8224690`](https://github.com/oimiragieo/tensor-grep/commit/822469015615bf06e715d9a35e5c5a745bbdb329))

- **release**: Require checklist verification commands
  ([`6699fc4`](https://github.com/oimiragieo/tensor-grep/commit/6699fc4adaf049733497d023cfa53066401ce87f))

- **release**: Require explicit package-manager rollback commands
  ([`881dd04`](https://github.com/oimiragieo/tensor-grep/commit/881dd047254bb46592d08e00ad980aaaf77a4116))

- **release**: Require release parity step presence in validator
  ([`2c555db`](https://github.com/oimiragieo/tensor-grep/commit/2c555dbef3bb88a75b5fdebd7e4345a9f5c0c9bb))

- **release**: Require tag/version flags in final parity steps
  ([`90bce79`](https://github.com/oimiragieo/tensor-grep/commit/90bce7963dffb365436a7a94d1ba234759d86493))

- **release**: Require verify-release-assets dependency contract
  ([`1328408`](https://github.com/oimiragieo/tensor-grep/commit/1328408e40d9e029c667350d5ac6899275f16937))

- **runbook**: Require executable rollback commands
  ([`627a7ab`](https://github.com/oimiragieo/tensor-grep/commit/627a7ab54f6802071510631c87cdf94e9b934ac3))


## v0.31.1 (2026-03-07)

### Bug Fixes

- **release**: Correctly scope baseline-auto checks per ci step
  ([`a593d83`](https://github.com/oimiragieo/tensor-grep/commit/a593d83b1c99d659848655057752d30a177aaf04))

### Testing

- **release**: Enforce auto baseline contract in ci validator
  ([`540236c`](https://github.com/oimiragieo/tensor-grep/commit/540236c6474ee018718ddf88fb6aa8050cdd8dc2))


## v0.31.0 (2026-03-07)

### Continuous Integration

- **bench**: Switch regression jobs to auto baseline resolution
  ([`baa9974`](https://github.com/oimiragieo/tensor-grep/commit/baa997473f7d2d29c3f377f896c7a63b8f2e90e6))

### Features

- **bench**: Add auto baseline selection for regression checks
  ([`aabb593`](https://github.com/oimiragieo/tensor-grep/commit/aabb59307e619f830f6a304dc33dcc0d36ed5278))

### Testing

- **pipeline**: Lock non-gpu routing guards for fixed/count/context modes
  ([`b0be220`](https://github.com/oimiragieo/tensor-grep/commit/b0be220ff39e1b7ad44d48ddc60e1092c1206c55))


## v0.30.4 (2026-03-07)

### Bug Fixes

- **bench**: Default gpu benchmark data path to artifacts
  ([`d3ccd8a`](https://github.com/oimiragieo/tensor-grep/commit/d3ccd8a7bb13573421cf22acd3dafb07525a4f6f))


## v0.30.3 (2026-03-07)

### Bug Fixes

- **cpu**: Suppress regex futurewarnings in python fallback
  ([`924a6e9`](https://github.com/oimiragieo/tensor-grep/commit/924a6e93058ebb0ea1782b4969fbd1d249cb6827))


## v0.30.2 (2026-03-07)

### Bug Fixes

- **bench**: Always emit ast benchmark artifact when sg is missing
  ([`fc34cd9`](https://github.com/oimiragieo/tensor-grep/commit/fc34cd9659137e613af2d897f18722eaba88cbc6))

### Documentation

- **bench**: Refresh benchmark tables and test counts
  ([`dae15a6`](https://github.com/oimiragieo/tensor-grep/commit/dae15a631a6de52689cf7ec7ed59bcacfceb1cbe))

### Testing

- **bench**: Lock environment-aware baseline and regression guard behavior
  ([`515585d`](https://github.com/oimiragieo/tensor-grep/commit/515585dbe5a6d959d18601405f75af23a5edcaeb))

- **cudf**: Lock distributed routing metadata contract
  ([`dafbe3f`](https://github.com/oimiragieo/tensor-grep/commit/dafbe3fcbba9017add076aa9c10a12f6cba69c4a))

- **installer**: Lock directory-restore contract for install scripts
  ([`ef21b6e`](https://github.com/oimiragieo/tensor-grep/commit/ef21b6ef28ec079d199aed7f826f09b8ffdfe77d))


## v0.30.1 (2026-03-06)

### Bug Fixes

- **stats**: Hide planned gpu ids when runtime falls back
  ([`6d3d0e6`](https://github.com/oimiragieo/tensor-grep/commit/6d3d0e623ed4f5d0fd3141619a0d211b1ba54914))


## v0.30.0 (2026-03-06)

### Bug Fixes

- **cli**: Prefer runtime routing metadata over planned backend
  ([`62dcc54`](https://github.com/oimiragieo/tensor-grep/commit/62dcc54b32f0c169d8bdb8777f2f0aec19f41b98))

### Features

- **debug**: Emit runtime routing fallback metadata
  ([`034d71c`](https://github.com/oimiragieo/tensor-grep/commit/034d71cfa105dab42c7e5dc89a4d3e29675d1322))

### Testing

- **cli**: Lock distributed routing metadata in json output
  ([`73082bd`](https://github.com/oimiragieo/tensor-grep/commit/73082bd1145c2a2824676ceaad1ac23912d3fa11))


## v0.29.3 (2026-03-06)

### Performance Improvements

- **torch**: Skip detector probe when gpu ids are explicitly pinned
  ([`5da4c0b`](https://github.com/oimiragieo/tensor-grep/commit/5da4c0bb17be1bc720008d2617547849257fa650))

### Testing

- **release**: Cover tg shim resolution for smoke installer
  ([`d4456c2`](https://github.com/oimiragieo/tensor-grep/commit/d4456c2b3f5da0364ac638c69f9f74d90dc4d482))


## v0.29.2 (2026-03-06)

### Documentation

- **bench**: Refresh benchmark tables from latest full local pass
  ([`10759c1`](https://github.com/oimiragieo/tensor-grep/commit/10759c1b95a4dc86334ab2b566ac77e318fd286d))

- **bench**: Refresh README and paper with latest benchmark run
  ([`5e98cfc`](https://github.com/oimiragieo/tensor-grep/commit/5e98cfcedc3a6f434530cd029c18835d41577cf8))

- **release**: Document final npm parity success gate
  ([`2a25af2`](https://github.com/oimiragieo/tensor-grep/commit/2a25af2f2cd56df1dead92c1f796fec65a4c1330))

### Performance Improvements

- **device**: Cache gpu availability and device count
  ([`30c1fc9`](https://github.com/oimiragieo/tensor-grep/commit/30c1fc9225c16486877c56b926906167d4fff462))

- **device**: Cache per-device vram capacity lookups
  ([`d4d0c94`](https://github.com/oimiragieo/tensor-grep/commit/d4d0c9428b25976d0c475346b65caa5f8fe88f21))


## v0.29.1 (2026-03-06)

### Bug Fixes

- **rust**: Align arrow crates with pyo3-arrow to resolve chrono conflict
  ([`09c5aaa`](https://github.com/oimiragieo/tensor-grep/commit/09c5aaafe9f6681eb41ec06d395813778ed89413))

- **rust**: Migrate PyO3 API calls for 0.24 compatibility
  ([`7681998`](https://github.com/oimiragieo/tensor-grep/commit/7681998b5fc255a9a91d5aa12e8ee3519e10208c))

### Continuous Integration

- Add dependency install retries for python matrix
  ([`4b63af8`](https://github.com/oimiragieo/tensor-grep/commit/4b63af84989ab675a2aec136b45b1198a56bee1f))

- Enforce always-run pypi parity gate for release version
  ([`64e97e5`](https://github.com/oimiragieo/tensor-grep/commit/64e97e5d30a4ad331da3032b4e6b71ca847e44e8))

- Skip publish parity gate when no release version is produced
  ([`c1def82`](https://github.com/oimiragieo/tensor-grep/commit/c1def829509413d88493ef4ab181db131cd72465))

### Documentation

- **bench**: Refresh benchmark results from latest local pass
  ([`20f0868`](https://github.com/oimiragieo/tensor-grep/commit/20f0868ff5410b626d6cff6bb53041ffdfc4cfe9))

- **release**: Codify npm parity and rollback runbook
  ([`d64d1fc`](https://github.com/oimiragieo/tensor-grep/commit/d64d1fce41121472ee2eebe7daf28ac841bd031c))


## v0.29.0 (2026-03-06)

### Features

- **obs**: Expose distributed worker metadata across search outputs
  ([`79a973b`](https://github.com/oimiragieo/tensor-grep/commit/79a973b7719b7c4c0fdf099ff5a24674c32c4619))


## v0.28.0 (2026-03-06)

### Features

- **torch**: Weight multi-gpu shard fanout by chunk-plan sizes
  ([`13a82d1`](https://github.com/oimiragieo/tensor-grep/commit/13a82d1513f5ad48005cd44e2a3a961e5ed26ddd))


## v0.27.0 (2026-03-06)

### Chores

- **format**: Fix ruff preview formatting in project plan
  ([`b0593dd`](https://github.com/oimiragieo/tensor-grep/commit/b0593dd352e020c39fe746e98257dfae8b6d3ba3))

### Continuous Integration

- **bench**: Relax regression threshold to 20 percent to reduce runner jitter flake
  ([`2115b8d`](https://github.com/oimiragieo/tensor-grep/commit/2115b8dd3c2968e22c05c9068f24c3ddd2bce195))

### Documentation

- **bench**: Refresh README and paper with latest CI benchmark artifacts
  ([`25c4ac8`](https://github.com/oimiragieo/tensor-grep/commit/25c4ac8ea12ca8bbe111a9c623f33b8101f957c5))

### Features

- **devices**: Expose routable device-id contract in inventory API
  ([`b2a95e1`](https://github.com/oimiragieo/tensor-grep/commit/b2a95e18e721613117743bb381b747ccee128f63))

### Testing

- **ci-release**: Require benchmark-regression dependency for release job
  ([`c071302`](https://github.com/oimiragieo/tensor-grep/commit/c0713022c5ed268568188a0dfcaf58ba8d96dbf5))

- add validator test for missing benchmark-regression in jobs.release.needs\n- enforce check via
  YAML parsing in release asset validator


## v0.26.6 (2026-03-05)

### Performance Improvements

- **cudf**: Skip process pool for single-worker distributed plans
  ([`f26cfbf`](https://github.com/oimiragieo/tensor-grep/commit/f26cfbfbad328f1c9e5422f9ecb935df9e4472c9))

- add TDD coverage for multi-chunk plans that collapse to one worker/device\n- execute sequential
  chunk processing when max_workers <= 1\n- update integration/unit assertions for new single-worker
  fast path

### Testing

- **release**: Require package-manager runbook command coverage
  ([`aa2ce9d`](https://github.com/oimiragieo/tensor-grep/commit/aa2ce9d6ef8e074768a2cbdece19fd71207a54ec))

- add TDD check for required publish/verification commands in package manager runbook\n- enforce
  command-level contract in release asset validator


## v0.26.5 (2026-03-05)

### Bug Fixes

- **ci**: Apply ruff preview formatting to tracked files
  ([`738b073`](https://github.com/oimiragieo/tensor-grep/commit/738b073ed4d76d50a6d5d702158486f805d0a5ec))

- format files using uff format --preview to match CI formatter mode\n- keep lint and targeted tests
  green after formatting

### Documentation

- **bench**: Refresh benchmark results on current main
  ([`79c7535`](https://github.com/oimiragieo/tensor-grep/commit/79c7535dda3d13702bf394d5a657f449cbaa43e9))

- rerun benchmark suite and update README/PAPER metrics/date/commit\n- apply ruff formatting updates
  required for CI format gate\n- validate with ruff check + targeted pytest modules

### Performance Improvements

- **cudf**: Dedupe device ids before distributed worker sizing
  ([`ff44a21`](https://github.com/oimiragieo/tensor-grep/commit/ff44a218dea5078f037b0b667833e9a4a52a1c82))

- add TDD case for duplicate GPU IDs in distributed execution\n- normalize device/chunk list by
  device id and keep max chunk per device\n- size ProcessPool workers by unique routable GPUs

- **pipeline**: Normalize gpu chunk plan before backend selection
  ([`01997c9`](https://github.com/oimiragieo/tensor-grep/commit/01997c9ed7291106f5bf24684958a974e7fcdda7))

- add TDD coverage for duplicate/invalid memory chunk plan entries\n- deduplicate device ids in
  pipeline and drop non-positive chunk sizes\n- keep largest chunk per device for stable backend
  worker sizing

### Testing

- **multi-gpu**: Assert duplicate ids collapse to single-worker fanout
  ([`60e2ea5`](https://github.com/oimiragieo/tensor-grep/commit/60e2ea5d3719a5a54dac0c3ce5640eab5f207994))

- add integration coverage for distributed cudf execution with duplicate device ids\n- verify
  ProcessPool worker sizing uses unique routable devices

- **release**: Enforce ci ruff preview formatter contract
  ([`9352dad`](https://github.com/oimiragieo/tensor-grep/commit/9352dad9a2ecb12b45663ee381963f184dd8477c))

- add release-assets validator test for CI uff format --check --preview requirement\n- enforce
  formatter preview mode in workflow contract checks to prevent local/CI drift


## v0.26.4 (2026-03-05)

### Performance Improvements

- **cudf**: Cap distributed worker pool to planned chunk count
  ([`c15ecd1`](https://github.com/oimiragieo/tensor-grep/commit/c15ecd1234ef7ff4c00ddebcdb736d0f9e74c5f6))


## v0.26.3 (2026-03-04)

### Performance Improvements

- **cudf**: Skip process pool when distributed plan has one chunk
  ([`3754ecd`](https://github.com/oimiragieo/tensor-grep/commit/3754ecd691de5d5d307c16508ec53b592fddd672))

### Testing

- **memory**: Ensure cached gpu id lists are returned immutably
  ([`d45d53b`](https://github.com/oimiragieo/tensor-grep/commit/d45d53b98ba49ca5dad6022926e51d8dcf352ff5))


## v0.26.2 (2026-03-04)

### Performance Improvements

- **memory**: Cache detected gpu ids between routing calls
  ([`2df9fae`](https://github.com/oimiragieo/tensor-grep/commit/2df9fae5a11f1f68c10f4285e34331e0511b58dd))

### Testing

- **release**: Cover package-manager url parity validation paths
  ([`e3b7d6c`](https://github.com/oimiragieo/tensor-grep/commit/e3b7d6c69c3a15bf2743925dc3a920772123ff14))


## v0.26.1 (2026-03-04)

### Bug Fixes

- **memory**: Ignore non-integer device count fallback values
  ([`7234eee`](https://github.com/oimiragieo/tensor-grep/commit/7234eeed9b19ea15a61041d62b28f6129394e5dd))

### Performance Improvements

- **memory**: Prefer direct gpu id lookup before device metadata enumeration
  ([`abf9d62`](https://github.com/oimiragieo/tensor-grep/commit/abf9d629ee51f3ab6a54f5c1d4877c48fbb4790a))

### Testing

- **release**: Enforce ci pypi publish job security contract
  ([`16891a1`](https://github.com/oimiragieo/tensor-grep/commit/16891a1def2287ba3eea357866f64c8ddb3a1938))

- **release**: Require ci pypi url and skip-existing publish contract
  ([`874a7e7`](https://github.com/oimiragieo/tensor-grep/commit/874a7e7f44eeca0b88c3340a7efc67f377aa7daa))


## v0.26.0 (2026-03-04)

### Code Style

- Apply ruff preview formatting to satisfy ci formatter gate
  ([`5c90e76`](https://github.com/oimiragieo/tensor-grep/commit/5c90e76f1af035cb1dd5d95ba5e10ffd9cc51429))

### Documentation

- Document mcp tg_devices and json routing metadata
  ([`84596ba`](https://github.com/oimiragieo/tensor-grep/commit/84596ba820cadaad26c24b97b87e90a3c1b7e17d))

- **bench**: Refresh benchmark results for latest routing line
  ([`cf7f2c8`](https://github.com/oimiragieo/tensor-grep/commit/cf7f2c8ea2d1a81026978cefa64383f85f4fecfe))

### Features

- **cli**: Add --format support to tg devices output
  ([`2533fb6`](https://github.com/oimiragieo/tensor-grep/commit/2533fb63b48f92bbf0e5fd9f2ee1e466c4d10d47))

### Refactoring

- **hardware**: Unify gpu inventory contract for cli and mcp
  ([`e5b8847`](https://github.com/oimiragieo/tensor-grep/commit/e5b88475d8644c77fb911e770cc88af72806c5e4))

- **torch**: Prefer stable gpu id enumeration api for routing
  ([`6362984`](https://github.com/oimiragieo/tensor-grep/commit/63629842f6d9a2f7841d02a04b62ec3479792278))

### Testing

- **cli**: Lock json routing metadata contract
  ([`eb2823a`](https://github.com/oimiragieo/tensor-grep/commit/eb2823acde75ce98f82720dde56feae01b655bce))

- **cli**: Lock main_entry routing for devices and raw patterns
  ([`4b38bab`](https://github.com/oimiragieo/tensor-grep/commit/4b38bab38e930f62784e3b56c23319d218235041))

- **cli**: Make devices format error assertion resilient to ansi rendering
  ([`b99d4dd`](https://github.com/oimiragieo/tensor-grep/commit/b99d4dd869dc5eda3bd39d422d8362aba39620dc))

- **devices**: Lock human-readable inventory output contract
  ([`0589c7e`](https://github.com/oimiragieo/tensor-grep/commit/0589c7e46eff1454926a233d0d540923098f220b))

- **hardware**: Cover default detector path in inventory helper
  ([`00e6265`](https://github.com/oimiragieo/tensor-grep/commit/00e62655d82b3b495efcbae7bb50ebc843f2dc6a))


## v0.25.0 (2026-03-04)

### Features

- **mcp**: Add tg_devices tool for GPU inventory
  ([`a109b89`](https://github.com/oimiragieo/tensor-grep/commit/a109b89fd362887d2eb00fbcf497fd9408f0f42f))


## v0.24.0 (2026-03-04)

### Features

- **json**: Include routing metadata in structured output
  ([`73b53b8`](https://github.com/oimiragieo/tensor-grep/commit/73b53b88f749c6028abafa1ba197aa0da1d456c8))

### Testing

- **snapshot**: Update json output snapshot for routing metadata
  ([`87de225`](https://github.com/oimiragieo/tensor-grep/commit/87de2255708503c21185a01c7ddc32c5cd805ff6))


## v0.23.0 (2026-03-04)

### Code Style

- **test**: Format cli device tests for CI
  ([`4bf4bf4`](https://github.com/oimiragieo/tensor-grep/commit/4bf4bf4d952ba9f7c78a0efaa2c9fd4d5c5b48c2))

### Documentation

- **bench**: Refresh benchmark tables for 9ec43ed pass
  ([`ebd182b`](https://github.com/oimiragieo/tensor-grep/commit/ebd182b5b44d3b9c71c6539624cbf30be3285069))

### Features

- **cli**: Add tg devices command for GPU inventory
  ([`9ec43ed`](https://github.com/oimiragieo/tensor-grep/commit/9ec43edbf12cbea4a92d7a6aa283cc467ccfd866))

- **mcp**: Include routing summary in tool responses
  ([`8b921a0`](https://github.com/oimiragieo/tensor-grep/commit/8b921a0c032285e116021ec10e8de5e20be9623d))

- **obs**: Surface gpu routing details in debug and stats output
  ([`da4545f`](https://github.com/oimiragieo/tensor-grep/commit/da4545f886f91ed2dc0878d1106f77d63d9e8be2))


## v0.22.0 (2026-03-04)

### Code Style

- Format release asset validator for CI
  ([`011c239`](https://github.com/oimiragieo/tensor-grep/commit/011c239dc8a64976ca911bfb808c93a256432b9f))

### Continuous Integration

- **parity**: Enforce package-manager version checks on publish gate
  ([`fb3e4f4`](https://github.com/oimiragieo/tensor-grep/commit/fb3e4f4331a793da4cb3b54e41ece113a2cec9b8))

- **release**: Remove invalid parity flag and add workflow guard tests
  ([`abeaedf`](https://github.com/oimiragieo/tensor-grep/commit/abeaedff9af103e53db69c4a8e71c91aa6897515))

### Documentation

- Refresh benchmark results for 985e303 rerun
  ([`3c3d342`](https://github.com/oimiragieo/tensor-grep/commit/3c3d342ac32613e518d07387d017426267964aa6))

### Features

- **obs**: Expose multi-gpu chunk plan metadata through pipeline/results
  ([`1e1d3b7`](https://github.com/oimiragieo/tensor-grep/commit/1e1d3b723571c09fc5bb0309d44545be660b1dc5))

### Testing

- **release**: Guard against parity skip flags in workflows
  ([`eaae45a`](https://github.com/oimiragieo/tensor-grep/commit/eaae45a9b7ba83bcf2a13b6b18b3ef2cd243c41b))


## v0.21.0 (2026-03-04)

### Continuous Integration

- Upload package-manager bundle artifacts from readiness checks
  ([`7217df5`](https://github.com/oimiragieo/tensor-grep/commit/7217df590abb6fb372fe9d5c90d1431b927c29a8))

- **release**: Enforce strict release asset matrix membership
  ([`a50b779`](https://github.com/oimiragieo/tensor-grep/commit/a50b779b7a548b27378b7f453f259f1e148a2a94))

- **release**: Reject unmanaged entries in checksum manifests
  ([`37128ac`](https://github.com/oimiragieo/tensor-grep/commit/37128ac21b7b21645646e7a40ad668dc459e611d))

- **release**: Smoke-run built binaries before artifact upload
  ([`5103aec`](https://github.com/oimiragieo/tensor-grep/commit/5103aecc733e1c0915f8ee7b257119fd6b295a9f))

### Features

- **obs**: Attach routing metadata to SearchResult
  ([`985e303`](https://github.com/oimiragieo/tensor-grep/commit/985e303cf396a9a05e5ca3ab7302abdbd962891c))


## v0.20.0 (2026-03-03)

### Continuous Integration

- Add terminal publish-success-gate to main release flow
  ([`84a51ad`](https://github.com/oimiragieo/tensor-grep/commit/84a51ad8d2220eeacaa114e5823e424fb6d606b7))

- Make package-manager bundle checks shell-safe and reduce python install flake
  ([`0e77c57`](https://github.com/oimiragieo/tensor-grep/commit/0e77c5788278c7227bd8d0c031d03072f2e1e8a1))

- Verify package-manager bundle checksums in main pipeline
  ([`07ac74a`](https://github.com/oimiragieo/tensor-grep/commit/07ac74a8ce39088da3daa9c764355efca0a94618))

- **release**: Add package-manager bundle checksums and verify assets
  ([`1ba5742`](https://github.com/oimiragieo/tensor-grep/commit/1ba57429ead3e7b693aea563dc85c5d27fe9f4a3))

- **release**: Add terminal publish success gate job
  ([`996f602`](https://github.com/oimiragieo/tensor-grep/commit/996f60286dbbf84613f29fbfc5766eb4ca1f1dd0))

- **release**: Enforce GitHub digest parity for release checksums
  ([`f8a8df2`](https://github.com/oimiragieo/tensor-grep/commit/f8a8df206cc5366fe9cd43e9b8c2e1fe495c3ebf))

- **release**: Publish package-manager bundle assets on tag builds
  ([`6e76a15`](https://github.com/oimiragieo/tensor-grep/commit/6e76a1518028533c9dd83cbb4865b6ba704b10b0))

- **release**: Require package-manager bundle assets in release verification
  ([`66552b8`](https://github.com/oimiragieo/tensor-grep/commit/66552b8b070c575010b623ba664063c6103b2d75))

- **release**: Validate bundle checksum manifest against release assets
  ([`b632582`](https://github.com/oimiragieo/tensor-grep/commit/b6325824f016aad8a1353fb4cd3d2e6bf73053ec))

- **release**: Verify package-manager bundle checksums before publish
  ([`4172517`](https://github.com/oimiragieo/tensor-grep/commit/417251733d436ad620749537bac7eac85eb251a9))

### Features

- **obs**: Expose selected GPU device ids on pipeline routing
  ([`4e97208`](https://github.com/oimiragieo/tensor-grep/commit/4e972081843323458e8d8cbac2c3ba9b3a165fa2))


## v0.19.0 (2026-03-03)

### Code Style

- **test**: Apply ruff preview formatting for release asset tests
  ([`6d296d1`](https://github.com/oimiragieo/tensor-grep/commit/6d296d1d0b05af4afa607c93687501f4b533d73f))

### Continuous Integration

- **release**: Enforce tag parity job wiring for publish gates
  ([`538205b`](https://github.com/oimiragieo/tensor-grep/commit/538205b7e46207b2638546ac87188debeec48a70))

- **release**: Gate docs publish on verified release assets
  ([`e210545`](https://github.com/oimiragieo/tensor-grep/commit/e21054569595c19a6059a25e318c922a7b85200f))

- **release**: Smoke-verify linux binary version before publish
  ([`b42d7eb`](https://github.com/oimiragieo/tensor-grep/commit/b42d7eb35c2e5342cf863b3bfcdeb7dd99428a3f))

### Documentation

- **bench**: Refresh benchmark tables for commit 538205b
  ([`04842db`](https://github.com/oimiragieo/tensor-grep/commit/04842db5dde941fd219edf54a41624588ef3d8f8))

### Features

- **gpu**: Execute torch multi-gpu fanout via per-device workers
  ([`a56dbb3`](https://github.com/oimiragieo/tensor-grep/commit/a56dbb3c0af796fa9d27f289201da1e8703338f7))

### Testing

- **release**: Enforce strict release asset and checksum matrix
  ([`2891efe`](https://github.com/oimiragieo/tensor-grep/commit/2891efe725ea535b791a121eeb884f3ba2d6c200))


## v0.18.0 (2026-03-03)

### Code Style

- **test**: Apply ruff preview formatting for release asset verifier tests
  ([`e60ad5a`](https://github.com/oimiragieo/tensor-grep/commit/e60ad5a8233fa2c14590e05e0e461b145d4121a5))

### Features

- **gpu**: Honor explicit device pinning in routing; refresh benchmarks
  ([`a554d83`](https://github.com/oimiragieo/tensor-grep/commit/a554d831ac9bb44ad436985c0dba3b1b3905e82d))

- **release**: Verify uploaded GitHub assets and checksum matrix
  ([`3a19da2`](https://github.com/oimiragieo/tensor-grep/commit/3a19da2a87e8d7fbc8539cdc2d5ff98fa4bf16c4))

### Testing

- **gpu**: Lock DeviceDetector multi-gpu id enumeration contract
  ([`a9ab730`](https://github.com/oimiragieo/tensor-grep/commit/a9ab7307a994147e221e6874347b3ad9d2e7328d))


## v0.17.0 (2026-03-03)

### Features

- **release**: Add package-manager publish bundle automation and CI checks
  ([`fb602b8`](https://github.com/oimiragieo/tensor-grep/commit/fb602b8a4b843a488f298e9289f129c2109708d1))

### Testing

- **release**: Enforce homebrew explicit-version formula contract
  ([`79b6405`](https://github.com/oimiragieo/tensor-grep/commit/79b64055ba4997bb50346863f1c8238a4eed4923))

- **release**: Enforce package-manager sections in installation docs
  ([`85143d9`](https://github.com/oimiragieo/tensor-grep/commit/85143d919f844b4fc671d36bc9d9e23230242b23))


## v0.16.2 (2026-03-03)

### Bug Fixes

- **release**: Make homebrew version bump deterministic via explicit variable
  ([`82e9000`](https://github.com/oimiragieo/tensor-grep/commit/82e9000256014012b83d478c72c069c44775a953))

- **release**: Sync homebrew formula to 0.16.1
  ([`5e7120b`](https://github.com/oimiragieo/tensor-grep/commit/5e7120bcf2bd23d550e25a7fd62e1eab46280699))

### Testing

- **ci**: Enforce pypi parity retry args in workflow validation
  ([`41c2f13`](https://github.com/oimiragieo/tensor-grep/commit/41c2f13c4d49705aa2ccb7b07ef3562cfcf7f51a))

- **release**: Enforce package-manager runbook/checklist sections
  ([`51334b8`](https://github.com/oimiragieo/tensor-grep/commit/51334b836d5e3117c3e235a2439e0e40b99ddd65))


## v0.16.1 (2026-03-03)

### Bug Fixes

- **ci**: Retry pypi parity check and lock preferred-id fanout path
  ([`79b0a4a`](https://github.com/oimiragieo/tensor-grep/commit/79b0a4af1d385c3218ac03d37b7e6e8c9a1de0e1))

- **release**: Sync homebrew formula to 0.16.0
  ([`8200524`](https://github.com/oimiragieo/tensor-grep/commit/8200524b25384ef91ae73df4916aeffe3bac2fb3))


## v0.16.0 (2026-03-03)

### Bug Fixes

- **release**: Sync homebrew formula to 0.15.1
  ([`d373e03`](https://github.com/oimiragieo/tensor-grep/commit/d373e0341fa3e78dfd01098ad61741f2ce9afc4a))

### Documentation

- **bench**: Refresh README and paper benchmark results
  ([`9031bba`](https://github.com/oimiragieo/tensor-grep/commit/9031bbaa7f640bfcaca0b73ea8454a3a727a26cb))

### Features

- **multi-gpu**: Add explicit device-id enumeration API contract
  ([`69ddc38`](https://github.com/oimiragieo/tensor-grep/commit/69ddc38b684792edb7abd5cfc31895ee89601352))


## v0.15.1 (2026-03-03)

### Bug Fixes

- **ci**: Scope publish parity gate to tag/core versions and pypi
  ([`4539963`](https://github.com/oimiragieo/tensor-grep/commit/4539963cd082d81887a3014503fea285ff8ae583))

- **ci**: Unflake parity tests and apply required formatting
  ([`d471452`](https://github.com/oimiragieo/tensor-grep/commit/d4714527b25cbe4391cb87ae724e69dec8d18432))

- **release**: Sync homebrew formula to 0.15.0
  ([`146103a`](https://github.com/oimiragieo/tensor-grep/commit/146103a4d31c15b1a04bb2cded63ccbb3b52e144))


## v0.15.0 (2026-03-03)

### Bug Fixes

- **release**: Sync homebrew formula to 0.14.1
  ([`c1d1142`](https://github.com/oimiragieo/tensor-grep/commit/c1d1142a9ea426f81fe56570e8eaef6decd563f4))

### Code Style

- **ci**: Apply ruff formatting for release parity validator
  ([`2151708`](https://github.com/oimiragieo/tensor-grep/commit/2151708fd6cd70f9319fb4e4b1c6d1cb0584a800))

### Continuous Integration

- **release**: Enforce consolidated version parity gate before publish success
  ([`ea0229b`](https://github.com/oimiragieo/tensor-grep/commit/ea0229bc71a259d8172c4f7c254a32bdcb78d207))

### Documentation

- **bench**: Refresh benchmark tables for latest main run
  ([`f0e9db3`](https://github.com/oimiragieo/tensor-grep/commit/f0e9db30a17a48e17f95528428487bb60c264500))

- **bench**: Refresh README and paper with latest benchmark pass
  ([`52166dd`](https://github.com/oimiragieo/tensor-grep/commit/52166ddaf6b2122e3bb8a4bba5e5a324493f32b5))

- **release**: Add package-manager publish and rollback runbook
  ([`df251f6`](https://github.com/oimiragieo/tensor-grep/commit/df251f671c1033b0e296fbd0a70b84a7516e6744))

### Features

- **gpu**: Wire torch fallback to selected multi-gpu device ids
  ([`03556bf`](https://github.com/oimiragieo/tensor-grep/commit/03556bfef61539109433d8e6620e859a5fe48c8e))

### Testing

- **gpu**: Lock mixed device-id normalization contract in pipeline
  ([`9292215`](https://github.com/oimiragieo/tensor-grep/commit/92922159c0c3adebe0e16a84b04a5e6f71a31188))

- **gpu**: Lock torch round-robin execution across selected device ids
  ([`3ebe12e`](https://github.com/oimiragieo/tensor-grep/commit/3ebe12edcb2437eed0cc174c7e3dafe890334a63))

- **torch**: Pin fake backend device ids to avoid detector cuda dependency
  ([`105f945`](https://github.com/oimiragieo/tensor-grep/commit/105f9451d33176884ee566f401f1a41804b1c86d))


## v0.14.1 (2026-03-03)

### Bug Fixes

- **ci**: Add pyyaml runtime dependency for release asset validator
  ([`b0c0c75`](https://github.com/oimiragieo/tensor-grep/commit/b0c0c75b36ed1dc11af54b33d9c6503899a1dc6e))

- **release**: Enforce structural winget checks and resync package assets 0.14.0
  ([`e85e125`](https://github.com/oimiragieo/tensor-grep/commit/e85e12541073a702d2eb1113800de29300ec789e))

- **release**: Stamp winget header version and lock with tests
  ([`2e98eff`](https://github.com/oimiragieo/tensor-grep/commit/2e98effcc4b5283c5a0c7d497f41cb692f794cff))


## v0.14.0 (2026-03-03)

### Code Style

- **cli**: Apply ruff preview formatting for rule spec construction
  ([`4fa4a87`](https://github.com/oimiragieo/tensor-grep/commit/4fa4a87283fd95e2dff5c644a2314921fcf6356e))

### Continuous Integration

- **release**: Validate binary artifact matrix and publish checksums
  ([`ffe1028`](https://github.com/oimiragieo/tensor-grep/commit/ffe1028afb0c3e7ddc177a9e2ec0c6e5e9465468))

### Features

- **cli**: Add --gpu-device-ids request pinning and sync brew 0.13.1
  ([`1e83b64`](https://github.com/oimiragieo/tensor-grep/commit/1e83b64634958b9dfd73aa7c0d8a2927fc660fa0))


## v0.13.1 (2026-03-03)

### Bug Fixes

- **release**: Auto-stamp brew and winget assets during semantic release
  ([`dac2b87`](https://github.com/oimiragieo/tensor-grep/commit/dac2b87a2474f266a10fe903e72ac47b3e9f3500))

### Documentation

- **bench**: Refresh benchmark tables; sync package assets to 0.13.0
  ([`ea8dbee`](https://github.com/oimiragieo/tensor-grep/commit/ea8dbee813d9bf6a337f164e1ed7b264cb2606ac))


## v0.13.0 (2026-03-03)

### Bug Fixes

- **ci**: Sync brew and winget release asset refs to 0.12.5
  ([`3dc6262`](https://github.com/oimiragieo/tensor-grep/commit/3dc6262ff2632349db0d1e34dd6e5a853155c219))

### Features

- **multi-gpu**: Add per-request device-id routing contract
  ([`eb249e4`](https://github.com/oimiragieo/tensor-grep/commit/eb249e43c6807eaa5c7fe36309d93b5a556282a3))


## v0.12.5 (2026-03-03)

### Bug Fixes

- **ci**: Sync brew and winget release assets to v0.12.4
  ([`d3442bd`](https://github.com/oimiragieo/tensor-grep/commit/d3442bd449a59a24cd96d50c713594c695c8f3b2))

### Documentation

- **release**: Add enterprise package-manager publish and rollback runbook
  ([`55f724b`](https://github.com/oimiragieo/tensor-grep/commit/55f724b880554f737e1f9ace6854771d02ea64bc))


## v0.12.4 (2026-03-03)

### Bug Fixes

- **ci**: Sync brew/winget manifests to v0.12.3
  ([`f0f4368`](https://github.com/oimiragieo/tensor-grep/commit/f0f436895e2e110e9e89d1db6138702103626cbf))

### Documentation

- **paper**: Assess STATIC constrained decoding fit for tensor-grep
  ([`144d578`](https://github.com/oimiragieo/tensor-grep/commit/144d5789cc724411c01454f5318e7782822dd779))


## v0.12.3 (2026-03-03)

### Bug Fixes

- **ci**: Sync winget and brew assets for v0.12.2
  ([`57aee00`](https://github.com/oimiragieo/tensor-grep/commit/57aee002d96665a9ed6fc9a8a373e7e4c57aa8b9))

### Continuous Integration

- Enforce semantic-release version stamping coverage
  ([`548f045`](https://github.com/oimiragieo/tensor-grep/commit/548f045d03a526451cb815fec8b55fd774c9f343))


## v0.12.2 (2026-03-03)

### Bug Fixes

- **ci**: Allow smoke install to resolve dependencies from index
  ([`17dca55`](https://github.com/oimiragieo/tensor-grep/commit/17dca551d157b06ec0a64e9b95738b0eff620856))

- **release**: Sync artifacts to v0.12.1 and auto-stamp release files
  ([`fbab25e`](https://github.com/oimiragieo/tensor-grep/commit/fbab25e51ddb2c845511669f7f8e23b77f3f3000))


## v0.12.1 (2026-03-03)

### Bug Fixes

- **ci**: Sync package manager artifacts to v0.12.0
  ([`dcb02db`](https://github.com/oimiragieo/tensor-grep/commit/dcb02db36d4d0c0d4631fdffbb16918904637683))

### Continuous Integration

- Add pypi artifact smoke install and hash-matrix checks
  ([`1177679`](https://github.com/oimiragieo/tensor-grep/commit/11776799ab86ec4efb03510ea1102a479bffdfa2))


## v0.12.0 (2026-03-02)

### Bug Fixes

- **ci**: Align package artifact versions and formatting
  ([`c653433`](https://github.com/oimiragieo/tensor-grep/commit/c653433e02f9bdf9098e71b0bdd4fd35c4ad0e29))

### Features

- Complete multi-gpu routing and harden pypi release checks
  ([`f5deae1`](https://github.com/oimiragieo/tensor-grep/commit/f5deae1d9f8269598d354c51471e4679ae9e06bf))


## v0.11.1 (2026-03-02)

### Bug Fixes

- **ci**: Build PyPI artifacts from semantic-release tag ref
  ([`17cfc0b`](https://github.com/oimiragieo/tensor-grep/commit/17cfc0bdc60b410cc9f4e9ba2454646db9f0dc95))

- **ci**: Correct workflow indentation for PyPI build jobs
  ([`bd5d800`](https://github.com/oimiragieo/tensor-grep/commit/bd5d8001ef6e0c88762dfd0598611a932083fe24))

- **release**: Resync package artifacts to 0.11.0
  ([`ef70ca3`](https://github.com/oimiragieo/tensor-grep/commit/ef70ca365d947c695b862e3016f0a70d0da990ac))


## v0.11.0 (2026-03-02)

### Bug Fixes

- **release**: Sync package manager and core versions to 0.10.1
  ([`fc4d6a5`](https://github.com/oimiragieo/tensor-grep/commit/fc4d6a55182cf8ef14d4f10b6e0b6bf0d0f41580))

### Features

- **multi-gpu**: Explicit device-id routing and release hardening checks
  ([`412bb33`](https://github.com/oimiragieo/tensor-grep/commit/412bb3377be01c61cda3294766d4d3858226f772))


## v0.10.1 (2026-03-02)

### Bug Fixes

- **ci**: Install uv in package-manager validation jobs
  ([`4c97de3`](https://github.com/oimiragieo/tensor-grep/commit/4c97de3e5b2ef609593f569a6eb024e61afeb258))

- **ci**: Resync release assets and refresh benchmark docs
  ([`a124395`](https://github.com/oimiragieo/tensor-grep/commit/a1243956f410131fc741ca39ba9fe8fd0e754e92))

### Continuous Integration

- **pkg**: Fallback when winget validate is unavailable
  ([`76ae896`](https://github.com/oimiragieo/tensor-grep/commit/76ae896c96e064195328fbadc27901883390ce20))

- **pkg**: Handle winget validate exit codes with fallback
  ([`c2c6663`](https://github.com/oimiragieo/tensor-grep/commit/c2c66637d70ac9a5d619f5688a38debd1c540790))

- **pkg**: Validate homebrew and winget manifests in ci/release
  ([`6e80ffb`](https://github.com/oimiragieo/tensor-grep/commit/6e80ffb30b6ef0d699f167414948b2c9edb2a09a))


## v0.10.0 (2026-03-02)

### Bug Fixes

- **release**: Align manifest and package versions to 0.9.1
  ([`8eab569`](https://github.com/oimiragieo/tensor-grep/commit/8eab56960f84a552dcfb79f06fbb5fc03dda7831))

### Features

- **multi-gpu**: Add explicit device-id fanout and release asset validation
  ([`27e8453`](https://github.com/oimiragieo/tensor-grep/commit/27e845394fbfc4f1d1bc1f15067d13a6c7c81fc2))


## v0.9.1 (2026-03-02)

### Bug Fixes

- **routing**: Keep invert-match on rust fast path
  ([`877a6ca`](https://github.com/oimiragieo/tensor-grep/commit/877a6ca0cd81a1987fac66cdaf1da137306d313d))

- **routing**: Prefer rg for context and boundary semantics
  ([`b7cea33`](https://github.com/oimiragieo/tensor-grep/commit/b7cea3381ef655ea44b737698e62c22a012e8c36))

- **rust-path**: Honor invert-match without python fallback
  ([`36f105c`](https://github.com/oimiragieo/tensor-grep/commit/36f105ccff16d2d910cbcf90c4c107b3ab05daeb))

### Continuous Integration

- **release**: Cancel stale runs to avoid non-fast-forward pushes
  ([`5004f41`](https://github.com/oimiragieo/tensor-grep/commit/5004f4105af60596bba9ee3cb0a0499b1a972d63))


## v0.9.0 (2026-03-02)

### Bug Fixes

- **cli**: Add precise yaml mapping type hints for mypy
  ([`81c116e`](https://github.com/oimiragieo/tensor-grep/commit/81c116e8939506af80501cd5f64346751e7f579c))

### Code Style

- Align ruff preview formatting with CI
  ([`60f5b20`](https://github.com/oimiragieo/tensor-grep/commit/60f5b207338081a7dc9465997cf03a3feb58dc23))

- **tests**: Apply ruff formatter for cybert backend tests
  ([`350dd76`](https://github.com/oimiragieo/tensor-grep/commit/350dd7616dbeb6bfec340cdff818d5039ee1a658))

### Features

- **cli**: Wire sgconfig scan and rule test execution
  ([`39e8148`](https://github.com/oimiragieo/tensor-grep/commit/39e81480eb094a2d7b569786a995dc8d635e4008))


## v0.8.0 (2026-03-02)

### Chores

- **gitignore**: Ignore local artifacts directory
  ([`ee957ff`](https://github.com/oimiragieo/tensor-grep/commit/ee957ffcbba4202a38a410873e87d2a792973be7))

- **repo**: Ignore artifacts and remove tracked profile_stats
  ([`770a772`](https://github.com/oimiragieo/tensor-grep/commit/770a772e86b483dcc6fe30afdb63c793651edcc8))

### Continuous Integration

- **hygiene**: Fail on tracked generated artifacts
  ([`8c40eff`](https://github.com/oimiragieo/tensor-grep/commit/8c40eff86ec3604a069b5e131508a3c4cd565f25))

### Features

- **cli**: Wire --stats output across search paths
  ([`4735ebe`](https://github.com/oimiragieo/tensor-grep/commit/4735ebea31c272eb66cb4cd61193e01a0662acee))


## v0.7.0 (2026-03-02)

### Features

- **cli**: Show backend routing reason in --debug output
  ([`b89a080`](https://github.com/oimiragieo/tensor-grep/commit/b89a080b998c5a8ed58afa200f76d90ac1e58da2))

### Testing

- **cli**: Cover tg upgrade dual-failure diagnostics
  ([`6259e24`](https://github.com/oimiragieo/tensor-grep/commit/6259e2477d909eeab30f0c699a51ec67909f2877))


## v0.6.1 (2026-03-02)

### Bug Fixes

- **installer**: Default stable channel to latest release
  ([`aa95068`](https://github.com/oimiragieo/tensor-grep/commit/aa9506848a60669a5c16399ae0b421804988929b))


## v0.6.0 (2026-03-02)

### Continuous Integration

- **bench**: Harden scheduled benchmark workflow
  ([`b0950f4`](https://github.com/oimiragieo/tensor-grep/commit/b0950f4098294ff9e36207f50fba40708927c1d4))

### Features

- **obs**: Add backend selection reason telemetry
  ([`6008eba`](https://github.com/oimiragieo/tensor-grep/commit/6008ebaff5852b6d067ca4298bacb7945beb2be9))


## v0.5.0 (2026-03-02)

### Build System

- **meta**: Publish README long description to PyPI
  ([`8f2488b`](https://github.com/oimiragieo/tensor-grep/commit/8f2488b4a640cdca86e8d6a0ff548f9069c833a8))

- **release**: Make sdist readme path compatible with maturin
  ([`678ec12`](https://github.com/oimiragieo/tensor-grep/commit/678ec12b410df69a2d3bc4773cc3818b22ad317d))

### Continuous Integration

- **pypi**: Only download release artifacts for publish
  ([`dd3eb90`](https://github.com/oimiragieo/tensor-grep/commit/dd3eb90f5066b2d623ee34effa4073d6ff30496c))

### Features

- **routing**: Allow gpu override for large complex regex
  ([`047b122`](https://github.com/oimiragieo/tensor-grep/commit/047b12279038f3248d69d11933ce34b486625a84))


## v0.4.1 (2026-03-02)

### Bug Fixes

- **cli**: Disable rg passthrough for --replace mode
  ([`02f034c`](https://github.com/oimiragieo/tensor-grep/commit/02f034c0ce2f9acd977bc9c031bedafeb3ca14a7))

### Continuous Integration

- **bench**: Enforce regression gate in main pipeline
  ([`9ca7ecb`](https://github.com/oimiragieo/tensor-grep/commit/9ca7ecbfc4b9ce500730315398a3199ad21f52ad))

- **bench**: Install rg and enforce pipefail in benchmark gate
  ([`ffca6b7`](https://github.com/oimiragieo/tensor-grep/commit/ffca6b736cd97a9f11b5048d9e73733cde038d99))

- **pypi**: Allow idempotent publish on reruns
  ([`6d332a3`](https://github.com/oimiragieo/tensor-grep/commit/6d332a396cadff8faa84694a768c605e51942145))


## v0.4.0 (2026-03-01)

### Continuous Integration

- **pypi**: Remove environment claim to match trusted publisher
  ([`76ecaf3`](https://github.com/oimiragieo/tensor-grep/commit/76ecaf3a243f341bfbd066926f3484cb1105afb6))

- **pypi**: Restore environment deployment tracking
  ([`299e6d3`](https://github.com/oimiragieo/tensor-grep/commit/299e6d3dd5796830568003182aa96e0adff5bed2))

### Features

- **ltl**: Add temporal query mode with CPU sequence evaluation
  ([`6b0a529`](https://github.com/oimiragieo/tensor-grep/commit/6b0a52986e497dbd5ae69c7703e5844217e30651))


## v0.3.4 (2026-03-01)

### Bug Fixes

- **installer**: Add linux/macos tg path shims and profile path wiring
  ([`6796338`](https://github.com/oimiragieo/tensor-grep/commit/679633836e6b8f9a87dad8f0a99c4c7b3a81fb7a))

### Chores

- **deps**: Remove invalid typer[all] extra
  ([`ef89e9b`](https://github.com/oimiragieo/tensor-grep/commit/ef89e9b4fda81dfcb68203509a6242e28f7bc4a6))

### Continuous Integration

- **release**: Publish PyPI directly from CI with OIDC and wheel matrix
  ([`34d256c`](https://github.com/oimiragieo/tensor-grep/commit/34d256c8d5f79e73d53b2640123a302a271be345))


## v0.3.3 (2026-03-01)

### Bug Fixes

- **cli**: Make tg upgrade work without pip in uv-managed envs
  ([`3b60ae1`](https://github.com/oimiragieo/tensor-grep/commit/3b60ae16eb27ded76a9f7b89c233a9c3119625f4))

### Documentation

- **benchmarks**: Refresh README and paper with 0.3.1 benchmark pass
  ([`d87601c`](https://github.com/oimiragieo/tensor-grep/commit/d87601ca1cc6d75721afa2f0a8c2b1a99c054b32))


## v0.3.2 (2026-03-01)

### Bug Fixes

- **installer**: Make tg resolve across new and no-profile shells
  ([`78afe8c`](https://github.com/oimiragieo/tensor-grep/commit/78afe8c9932082ec5866fd705f0649035ca076aa))


## v0.3.1 (2026-03-01)

### Bug Fixes

- **ci**: Stabilize routing tests and installer alias/version resolution
  ([`5355390`](https://github.com/oimiragieo/tensor-grep/commit/53553903abe5a95b775606fbfb0bc6399d51fec2))

### Documentation

- Refresh benchmark results from full benchmark pass
  ([`91d4c4b`](https://github.com/oimiragieo/tensor-grep/commit/91d4c4bc5e4109215211d6afe45dbeb666811121))

### Performance Improvements

- Add ripgrep passthrough, benchmark regression gates, and observability hooks
  ([`8c67f14`](https://github.com/oimiragieo/tensor-grep/commit/8c67f140072acac597374585408f4d8aef8a158b))

- **router**: Prefer rg passthrough then rust with GPU heuristics
  ([`21bc9a5`](https://github.com/oimiragieo/tensor-grep/commit/21bc9a5068446b803465a1bf70a1dfbf6f681ccb))


## v0.3.0 (2026-03-01)

### Bug Fixes

- **install**: Restore caller directory after powershell install
  ([`5082450`](https://github.com/oimiragieo/tensor-grep/commit/50824504c179700c91382af64c2793f8d81ce28c))

### Chores

- **gitignore**: Ignore local benchmark and rust installer artifacts
  ([`bcf49ef`](https://github.com/oimiragieo/tensor-grep/commit/bcf49effa351eff996c12c8aceb2300b2ac6ce4e))

### Documentation

- Document installer channels and changelog update
  ([`6a77f01`](https://github.com/oimiragieo/tensor-grep/commit/6a77f01aba1cf67be286129fd416f2ce1401c0b3))

### Features

- **installer**: Default to pinned stable release with optional main channel
  ([`067c5af`](https://github.com/oimiragieo/tensor-grep/commit/067c5aff98e5023247a50889464b838fd9b8a81f))


## v0.2.2 (2026-03-01)

### Bug Fixes

- Harden backends, wire CLI modes, stabilize benchmarks and CI
  ([`c0cbdf6`](https://github.com/oimiragieo/tensor-grep/commit/c0cbdf6c8d423149cf585ee5606464ce2d2a3aa6))

- **build**: Downgrade cargo edition from 2024 to 2021 to support maturin in semantic-release
  ([`cb46e85`](https://github.com/oimiragieo/tensor-grep/commit/cb46e857a1d4de8e39f6a5d1b00b7f956157a684))

- **build**: Downgrade Cargo.lock version from 4 to 3 to support older cargo in semantic-release
  ([`b6cf03e`](https://github.com/oimiragieo/tensor-grep/commit/b6cf03ee368810dfb0483556991134e39cac1c91))

- **build**: Downgrade pyo3 and pyo3-arrow to avoid edition 2024 dependencies
  ([`225566c`](https://github.com/oimiragieo/tensor-grep/commit/225566c7245da5527b25e105c4dcd1ca53bc6a96))

- **build**: Downgrade pyo3 from 0.24 to 0.22 to avoid wit-bindgen edition 2024 issue
  ([`f142bec`](https://github.com/oimiragieo/tensor-grep/commit/f142becb820f9a258ad3b77366b96890ad1af441))

- **build**: Downgrade pyo3-arrow again
  ([`fafd148`](https://github.com/oimiragieo/tensor-grep/commit/fafd1485709bc0cea0a30141ad726b9e2f3dce80))

- **build**: Enable PyO3 abi3 compatibility for universal Python wheels across 3.11+
  ([`086bfef`](https://github.com/oimiragieo/tensor-grep/commit/086bfeff15ca825f01eaffdfe9f96fc5053752c1))

- **build**: Re-add arrow dependencies
  ([`a2f28e9`](https://github.com/oimiragieo/tensor-grep/commit/a2f28e998fe17d2d9d5c07016c33c26db2cd76ec))

- **build**: Remove Cargo.lock to force maturin to generate one compatible with its older cargo
  version
  ([`daa2908`](https://github.com/oimiragieo/tensor-grep/commit/daa29080385e849a7bbe13932cc92a3ff9d5f315))

- **build**: Revert pyo3 downgrade
  ([`62020ca`](https://github.com/oimiragieo/tensor-grep/commit/62020cabb212111d136975cc2ac745bb423f3427))

- **build**: Update python api calls for pyo3 0.22
  ([`4522867`](https://github.com/oimiragieo/tensor-grep/commit/452286747c606e0ea3074fe33ce838313002fce6))

- **ci**: Resolve clippy and python matrix failures
  ([`d0cab71`](https://github.com/oimiragieo/tensor-grep/commit/d0cab71575a94eb50e640e7a9296a4a719eaf5ce))

- **release**: Use modern rust toolchain for semantic-release build
  ([`3dc875f`](https://github.com/oimiragieo/tensor-grep/commit/3dc875f4d423badf3642717c7be52217df68ca8e))

- **rust**: Replace let chains and fix borrow lifetime issue after downgrading cargo edition to 2021
  ([`7e8f282`](https://github.com/oimiragieo/tensor-grep/commit/7e8f282480fb2c47074d6e99c2c4bf683304def5))

- **rust**: Silence pyo3 clippy false positives
  ([`d0bd2e3`](https://github.com/oimiragieo/tensor-grep/commit/d0bd2e38473010979dc615c6577e20ff53b6f41d))

### Chores

- Sync versions across package files and setup semantic release variables
  ([`dbb50a1`](https://github.com/oimiragieo/tensor-grep/commit/dbb50a1fc05bebf3f31b73b3cec9f0118156c041))

- **rust**: Allow clippy useless_conversion in pyo3 module
  ([`90a74c3`](https://github.com/oimiragieo/tensor-grep/commit/90a74c3fac66579767fa26b787b26b6fc90fe5be))

### Code Style

- Fix rust formatting
  ([`07d1111`](https://github.com/oimiragieo/tensor-grep/commit/07d1111929c504337de8653306bfdf7dd65e0af4))

### Continuous Integration

- Automate releases with semantic-release
  ([`0bd48d8`](https://github.com/oimiragieo/tensor-grep/commit/0bd48d8712e230e7a7fcdf916c70560a7fd98886))

- Bump python-semantic-release to v9 to fix debian repository errors
  ([`6b270dc`](https://github.com/oimiragieo/tensor-grep/commit/6b270dc052cd1f31fefe379a812a7cf255fd4c91))

- Fix semantic-release v9 version variables format again
  ([`5f0cc97`](https://github.com/oimiragieo/tensor-grep/commit/5f0cc974d9e788d495368493be229df373d79b1e))

- Fix semantic-release v9 version_variables array format
  ([`2ea83ca`](https://github.com/oimiragieo/tensor-grep/commit/2ea83caf8117b4c88d92fbe509da2568800d1500))

- Fix uv not found in semantic-release isolated container by installing it first
  ([`8c371fb`](https://github.com/oimiragieo/tensor-grep/commit/8c371fba520a89b53dd562a25a610e4e41e96c53))


## v0.2.1 (2026-02-28)

### Bug Fixes

- Resolve CLI command path in tests and remaining linting errors
  ([`cf60f34`](https://github.com/oimiragieo/tensor-grep/commit/cf60f34d0689e4ea49b87360a00fb762781d9be8))

- Replaced 	g entrypoint calls with sys.executable -m tensor_grep.cli.main in e2e tests to ensure
  they work consistently across CI environments where the entrypoint script might not be on the PATH
  but the module is importable. - Fixed a remaining ruff whitespace issue in cybert_backend.py. -
  Fixed an import order issue in 	est_cybert_backend.py.

- Resolve formatting, typing, and test execution issues
  ([`b736354`](https://github.com/oimiragieo/tensor-grep/commit/b73635437f0deed76206c1c27c4152190ca9d5f5))

- Fixed Ruff whitespace and loop variable linting errors. - Fixed exception raising inside xcept
  clauses to use rom e. - Fixed mypy untyped decorator warnings for MCP tools. - Resolved numpy
  ImportError due to multiple imports in tests. - Added stringzilla backend to pyproject
  dependencies for test suite.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **core**: Support both old and new signatures of rust_core.search and ensure tests don't fail due
  to signature mismatch
  ([`c78b73e`](https://github.com/oimiragieo/tensor-grep/commit/c78b73e25a4d721c3b3c16497fad5848a3996f8e))

- **cybert**: Handle generic exception in opentelemetry context too to fix python 3.12+
  ([`73edbdf`](https://github.com/oimiragieo/tensor-grep/commit/73edbdf643b72599ff9ec990f3068c1da5646fd9))

- **cybert**: Handle generic exceptions in opentelemetry imports to fix test compatibility on Python
  3.12+
  ([`edbbee6`](https://github.com/oimiragieo/tensor-grep/commit/edbbee6a3ae12ce2c9e3c2c26e0bf77e63dc08bf))

- **cybert**: Handle mocked inference exception fallback correctly when opentelemetry is unavailable
  ([`5fadc2e`](https://github.com/oimiragieo/tensor-grep/commit/5fadc2eb04aab58fab72ffbf7162f457162fdbd9))

- **deps**: Pin importlib-metadata>=8.5.0 for Python < 3.12 to fix OpenTelemetry compatibility
  ([`4ea0092`](https://github.com/oimiragieo/tensor-grep/commit/4ea00922e0d43942f9982792ce1244c2e18ea41e))

- **io**: Skip rust directory scanner for single files
  ([`b58128b`](https://github.com/oimiragieo/tensor-grep/commit/b58128b2082d2c83b8c3f565bc98f4925385f94f))

- **lint**: Add types-PyYAML to dev dependencies to fix mypy error
  ([`e092e3a`](https://github.com/oimiragieo/tensor-grep/commit/e092e3a461018f234b6ffcc46f42d796280db6c0))

- **lint**: Fix ruff formatting errors
  ([`a99adfc`](https://github.com/oimiragieo/tensor-grep/commit/a99adfc413851fcfd78b1e18cc3a3234f02eac3a))

- **lint**: Fix ruff formatting errors
  ([`cec8f3b`](https://github.com/oimiragieo/tensor-grep/commit/cec8f3bbe789f2f64896c85dbf442b3a5e13614c))

- **lint**: Fix ruff trailing whitespace issues
  ([`bc6984c`](https://github.com/oimiragieo/tensor-grep/commit/bc6984c9b58dc052dd18d3ec986b5abd9138c860))

- **lint**: Ruff formatting issues in memory_manager
  ([`598fdfd`](https://github.com/oimiragieo/tensor-grep/commit/598fdfd915220718f94a5ff8a94c150b3ba7695f))

- **lint**: Run ruff format on memory_manager.py
  ([`dca7953`](https://github.com/oimiragieo/tensor-grep/commit/dca7953e43dea0d3dfc20ffa489fd11b97b38f58))

### Chores

- Bump version to 0.2.1
  ([`62d0a4f`](https://github.com/oimiragieo/tensor-grep/commit/62d0a4fbf773e447ebabc533061116b050ff21d1))

- **deps**: Pin importlib-metadata < 8.5.0 for Python < 3.12 to fix OpenTelemetry test compatibility
  ([`398ec0b`](https://github.com/oimiragieo/tensor-grep/commit/398ec0b0bf86b4a5c34ed4b0391118b64690370d))

- **deps**: Update opentelemetry requirement due to importlib-metadata bug
  ([`8dcadfe`](https://github.com/oimiragieo/tensor-grep/commit/8dcadfeb783ecc4bde71d1fb0fab55760d89d71e))

### Code Style

- Run ruff format
  ([`98a3a7d`](https://github.com/oimiragieo/tensor-grep/commit/98a3a7d6c404256f39b2bb844839745440dbed9b))

### Features

- **core**: Fallback to smart system RAM chunking if VRAM is 0
  ([`a95c121`](https://github.com/oimiragieo/tensor-grep/commit/a95c12142ce00034fcc553e84f2380780f3e2b30))

Improves performance of CPU fallback mode on machines with no GPU, preventing it from processing
  tiny 1MB chunks and generating too many processes.

### Testing

- Normalize line endings in json output snapshot test to fix cross-platform CI failure
  ([`e44664f`](https://github.com/oimiragieo/tensor-grep/commit/e44664ffaaa433800e6dc241911fed3bc356e796))

- **deps**: Add psutil to dev dependencies for tests
  ([`4786d80`](https://github.com/oimiragieo/tensor-grep/commit/4786d80f57e9ce1515e6a92e5c82ad01308db156))

- **e2e**: Deeply strip \r\n and \r from json_output snapshot to fix CI on windows/mac
  ([`6aad66a`](https://github.com/oimiragieo/tensor-grep/commit/6aad66a80799a12c97b109cd2129a6eba3d3db7b))

- **e2e**: Handle windows path replacements robustly
  ([`9e4c00a`](https://github.com/oimiragieo/tensor-grep/commit/9e4c00aa1edc6d92bacb32d3b008465f93dc1168))

- **e2e**: Robustly handle JSON escaped paths in snapshot to fix windows CI
  ([`5201b92`](https://github.com/oimiragieo/tensor-grep/commit/5201b92210806a544c4b51c52c371d61bcfeb302))

- **e2e**: Robustly handle JSON escaped paths in snapshot to fix windows CI
  ([`a89662a`](https://github.com/oimiragieo/tensor-grep/commit/a89662a1f755e67c8a95ace80fd3593542f751ee))

- **unit**: Update test_memory_manager zero vram to mock system RAM
  ([`d876c8c`](https://github.com/oimiragieo/tensor-grep/commit/d876c8cba0377f3dc8b017e8ea9accafca5c7cf4))


## v0.2.0 (2026-02-28)

### Bug Fixes

- Code formatting and ensure hybrid pipeline parity tests pass
  ([`039faee`](https://github.com/oimiragieo/tensor-grep/commit/039faeefef91d089470868ecb412ca8d6fa78cc7))

- Security audit - resolve type errors, test failures, and backend logic issues
  ([`b2dae1f`](https://github.com/oimiragieo/tensor-grep/commit/b2dae1f451c416214088ffc857af8f1396082517))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **ci**: Resolve ruff variable unpacking and mypy strict typing in pipeline
  ([`ebfb567`](https://github.com/oimiragieo/tensor-grep/commit/ebfb5676161fbac623d9c38a30bd286650c0020b))

- **lint**: Remove unused random import in parity corpus script
  ([`2862a50`](https://github.com/oimiragieo/tensor-grep/commit/2862a506a4bc8371a9d7aa65ca26870c325cd14b))

- **python**: Add Exception catch block to pycapsule chunked fallback in cudf backend
  ([`e13da0c`](https://github.com/oimiragieo/tensor-grep/commit/e13da0c0038ed5cbf34565d66100cce44afbc47f))

- **python**: Fallback to pure python when arrow PyCapsule conversion fails in mocked testing
  environments
  ([`a377ea3`](https://github.com/oimiragieo/tensor-grep/commit/a377ea3a8f829212d66ed5d4ed7539d66a8d0083))

- **python**: Handle transformers spec missing in python 3.14 importlib
  ([`277d395`](https://github.com/oimiragieo/tensor-grep/commit/277d3952009c2afc1e01d63f2ee6da4255b35db3))

- **rust**: Resolve pyo3 arrow trait bound mismatches and format code
  ([`91a9dc7`](https://github.com/oimiragieo/tensor-grep/commit/91a9dc7f556039d29e033345fcf2a381e2bc7d97))

- **rust**: Run cargo fmt to resolve github actions ci failure
  ([`d993f22`](https://github.com/oimiragieo/tensor-grep/commit/d993f2266ce200cf24ca19fbc8e7b1c31399fc37))

### Chores

- Bump version to 0.2.0 and decouple PyPI publish from Nuitka standalone build
  ([`be40266`](https://github.com/oimiragieo/tensor-grep/commit/be402666c5e0aecda02b70d4561a5cf7bd983f7e))

- Clean root directory moving logs and scripts
  ([`dc58dbd`](https://github.com/oimiragieo/tensor-grep/commit/dc58dbd7fc767fff640aaafc19a0a68d3c315427))

- Document PyO3 FFI overhead limits and revert to native CPython directory scanning
  ([`b2f3fdd`](https://github.com/oimiragieo/tensor-grep/commit/b2f3fdd07cf5785200eadd07ecd3111ea01a15d7))

- Fix torch loading and ctypes struct issues, document benchmarks
  ([`0042e7d`](https://github.com/oimiragieo/tensor-grep/commit/0042e7d3f2d9c461bdacce097d2ac16bf89d19f5))

- Remove accidentally tracked large generated artifacts and build targets from index
  ([`40480b3`](https://github.com/oimiragieo/tensor-grep/commit/40480b38977add180a777ca306455330f1f78ad8))

- Update gitignore to ignore rust targets, temp logs, and test artifacts
  ([`f540c09`](https://github.com/oimiragieo/tensor-grep/commit/f540c09d2c88f67ba4b9694a4932668f406d1dd7))

- **release**: V0.2.0
  ([`80e16f3`](https://github.com/oimiragieo/tensor-grep/commit/80e16f36ee9f69bf9a38e22bc896f108272b907c))

### Code Style

- Fix ruff formatting in main.py and add SKILL.md reference to README
  ([`d328584`](https://github.com/oimiragieo/tensor-grep/commit/d32858402598cd12decd40a645b4ef818ffb9712))

### Continuous Integration

- Fix workflow permissions
  ([`224dbaf`](https://github.com/oimiragieo/tensor-grep/commit/224dbaf5060650267ee93e68f97c194006f1935f))

- Added explicit read permissions to jobs in GitHub Actions workflows to fix CodeQL medium severity
  alerts.

### Documentation

- Add MCP documentation to README and CLI help
  ([`5a1f966`](https://github.com/oimiragieo/tensor-grep/commit/5a1f966bf3018ecc6fc9a598bf4b55314a7f1574))

- Add SKILL.md prompt file for AI assistant integrations
  ([`3f76413`](https://github.com/oimiragieo/tensor-grep/commit/3f76413d414cfd428acbbe645ec05f22b7e150b7))

- Formalize dynamic NVML chunking and Zero-Copy cuDF subword tokenization in academic paper
  ([`12c1714`](https://github.com/oimiragieo/tensor-grep/commit/12c1714a51b86f70c868fbcc07e72ffc7ef8e713))

- Update paper and changelog to reflect arrow zero-copy architecture and vram chunking
  ([`7b804e2`](https://github.com/oimiragieo/tensor-grep/commit/7b804e24ecf6acf6c476f175a93abe76702f7922))

- Update PAPER.md and README.md with real-world hybrid routing transparency and C:\dev benchmarks
  ([`3eba796`](https://github.com/oimiragieo/tensor-grep/commit/3eba796aacee980b8033d0077f1e9afebb3c60d1))

- Update README with in-place mutation features and finalize comprehensive benchmark suite
  ([`6d73013`](https://github.com/oimiragieo/tensor-grep/commit/6d7301350e38b4bb8d3fd62fe2c44f5a31fb9349))

### Features

- Add TDD implementations for StringZilla SIMD matching and cuDF JIT regex kernels
  ([`67c5695`](https://github.com/oimiragieo/tensor-grep/commit/67c56957495995e6ccffdebebde9f866f347c77c))

- Expose all complex CLI switches to AI via MCP and update SKILL.md docs
  ([`8c31222`](https://github.com/oimiragieo/tensor-grep/commit/8c31222573eaf7ff5c75c3e230022b8b8278a7bd))

- Implement LSP server with VRAM tensor caching and wire up ast-grep parity commands
  ([`b9f1ac0`](https://github.com/oimiragieo/tensor-grep/commit/b9f1ac066bc845255f9b9baa4d0ac45fb2cfe632))

- Initial Rust CLI orchestrator port using PyO3 embedding and ultra-fast CPU fallback beating
  ripgrep
  ([`60218c4`](https://github.com/oimiragieo/tensor-grep/commit/60218c44b1e7ad0e39f367a1f832c8acc0b7de93))

- Intelligent hybrid routing wrapping ripgrep and ast-grep binaries
  ([`0fac7d8`](https://github.com/oimiragieo/tensor-grep/commit/0fac7d832bd3ad238358d341e0f7ade4e426ea57))

- Security and observability updates for v1.0.0
  ([`2aeab77`](https://github.com/oimiragieo/tensor-grep/commit/2aeab77a0fd03bb013eae0f3b76b90aa3d9cf7a7))

- Added Dockerfile.gpu for hardened NVIDIA container toolkit deployment - Implemented OpenTelemetry
  tracing in cyBERT backend for observability - Updated CPUBackend to route python requests through
  Rust regex for ReDoS protection - Updated V1_RELEASE_PLAN.md with Docker container step

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **cli**: Add ripgrep-compatible version parity format
  ([`291954d`](https://github.com/oimiragieo/tensor-grep/commit/291954d555dd577a9500fca2986d9de3949d6108))

- **gpu**: Implement chunked pycapsules and vram-native cuDF tokenization
  ([`cd2de89`](https://github.com/oimiragieo/tensor-grep/commit/cd2de895203799de05fd2d5be4f0f9b793031847))

- **gpu**: Implement zero-copy PyCapsule ingestion for cuDF VRAM routing
  ([`9475748`](https://github.com/oimiragieo/tensor-grep/commit/94757486cf52e6f12f4838a4d2028661863dbf70))

- **replace**: Validate world-class multi-line & capture-group replacement speed and precision
  ([`35d61e6`](https://github.com/oimiragieo/tensor-grep/commit/35d61e622352dbb0e62906d9f35bdcac097b3ded))

- **rust**: Add highly requested --replace flag for native in-place log and code modification
  ([`ad4df4e`](https://github.com/oimiragieo/tensor-grep/commit/ad4df4e992c97b9911dc2c3ed9845b80755c9979))

- **rust**: Enable native file output extraction and update Paper with C:\dev recursive benchmark
  beating ripgrep
  ([`b993432`](https://github.com/oimiragieo/tensor-grep/commit/b993432c1a0c6a91d8bc8882a3671ab635bfb209))

- **rust**: Implement end-to-end zero-copy mmap to arrow c data interface
  ([`a515006`](https://github.com/oimiragieo/tensor-grep/commit/a515006a6f635572bc5471f2fecdba19a36aa6da))

- **rust**: Port DirectoryScanner to Rust using BurntSushi/ignore crate for lightning fast traversal
  ([`89dc271`](https://github.com/oimiragieo/tensor-grep/commit/89dc271ca2f3c2456d099d9cbae064c3e7c96682))

- **rust**: Wire all Python Typer subcommands (mcp, ast, lsp) through native clap fallback
  ([`06bea90`](https://github.com/oimiragieo/tensor-grep/commit/06bea9068ddd6db137d9c0e5e08ca32c34a2744b))

### Performance Improvements

- **cudf**: Ensure VRAM OOM safety with NVML dynamic chunking and explicit garbage collection
  ([`23cd52b`](https://github.com/oimiragieo/tensor-grep/commit/23cd52b21bb39630ff54ba389a2d845fe3c36f7b))

### Testing

- Add parity check script and fix backend routing for unsupported ripgrep flags
  ([`91031b1`](https://github.com/oimiragieo/tensor-grep/commit/91031b19587e2cf66bb6fc678b8a911dba5d07e0))

- Fix test_cudf_backend to correctly mock rust_core in CI environment where pyarrow is installed
  ([`54602d6`](https://github.com/oimiragieo/tensor-grep/commit/54602d615fd05f18f6e599e42097e54e6cc756f0))


## v0.1.5 (2026-02-26)

### Chores

- Bump version to 0.1.5
  ([`c7e5a63`](https://github.com/oimiragieo/tensor-grep/commit/c7e5a63603f03574ccb0cfb1a0273fb62f564cb7))

### Continuous Integration

- Fix pyo3 python 3.14 compat in maturin wheels
  ([`d4595f3`](https://github.com/oimiragieo/tensor-grep/commit/d4595f3818b07ad3281a679f8dbf7bb3027be479))

- **release**: Automate PyPI publishing using maturin cross-compilation
  ([`dd5e1e4`](https://github.com/oimiragieo/tensor-grep/commit/dd5e1e4dac23f342ad47f8e83caff2c6d4ca4c25))

### Documentation

- Add CONTRIBUTING checklist and update CHANGELOG for MCP feature
  ([`4a0f35e`](https://github.com/oimiragieo/tensor-grep/commit/4a0f35e3aa096e834a311eac22f43af378d7a53b))

### Features

- **mcp**: Implement AI-ready Model Context Protocol server capabilities
  ([`631b3e1`](https://github.com/oimiragieo/tensor-grep/commit/631b3e1c04bc44284c2a3c12c289c797b79fbaf7))


## v0.1.4 (2026-02-26)

### Bug Fixes

- **ci**: Fix PyO3 Py3.14 macOS compilation error, ensure ruff runs in venv, and fix CuDF backend
  mock contract tests
  ([`e7c7fef`](https://github.com/oimiragieo/tensor-grep/commit/e7c7fef7b7439b3e9b4299ef984d167da3348208))

- **ci**: Remove stale root build_binaries.py, add python linker for PyO3 on macos rust core tests,
  and force GPU allocation in cuDF availability check
  ([`c554c30`](https://github.com/oimiragieo/tensor-grep/commit/c554c30c921fecc642368a5ee2542dad1b0eda61))

- **ci**: Resolve all mypy strict typing errors and PyO3 macOS linker path
  ([`c0193bc`](https://github.com/oimiragieo/tensor-grep/commit/c0193bc07bde9ec6561948833f80f9b00e61301b))

- **ci**: Resolve Ruff strict typing format, MacOS test python linkage, and cuDF fallback mock tests
  ([`363a54a`](https://github.com/oimiragieo/tensor-grep/commit/363a54a7e523d41f2d179f99e0f0149f5a862917))

- **installer**: Split index url args for uv compatibility
  ([`5470b03`](https://github.com/oimiragieo/tensor-grep/commit/5470b03c3a042c480b378ad9a860ccd8f9693e31))

- **python**: Replace strict torch typing with forward references to prevent NoneType attribute
  errors when torch is uninstalled
  ([`8d1c040`](https://github.com/oimiragieo/tensor-grep/commit/8d1c0408d71b19e23f72f7213f503c3a67f53075))

- **python**: Suppress GPU execution on virtual CI nodes and add numpy dev fallback for cyBERT
  ([`815a16d`](https://github.com/oimiragieo/tensor-grep/commit/815a16d51534bb7c68e461be88286adff41d4f70))

- **rust**: Add missing fixed_strings CLI argument to resolve compilation failure in main.rs
  ([`f6012d2`](https://github.com/oimiragieo/tensor-grep/commit/f6012d2e1c79d9ff7cec72c4d52d4599fa4704f8))

- **typing**: Explicitly type return values for boolean/int functions to satisfy CI mypy
  ([`975de94`](https://github.com/oimiragieo/tensor-grep/commit/975de94c04b9e4e4aa67540d59317afbe94a924c))

### Chores

- Bump version to 0.1.4
  ([`6c3c0a8`](https://github.com/oimiragieo/tensor-grep/commit/6c3c0a8d3ccb13f36d057ef4f3afad675b7a8bc0))

- **typing**: Disable warn_unused_ignores to support different dependency environments
  ([`b49f2d2`](https://github.com/oimiragieo/tensor-grep/commit/b49f2d21e678b6dee40a760f1267590695794af9))

### Code Style

- Apply ruff format and linting fixes to newly added code
  ([`fbac202`](https://github.com/oimiragieo/tensor-grep/commit/fbac2026df1480979db621a0babecb3331a8534e))

- Apply ruff formatting to backends
  ([`c46a2e3`](https://github.com/oimiragieo/tensor-grep/commit/c46a2e301d3c9b7d2cf32bad43d083c5912dbaf1))

- **python**: Fix ruff format warnings to resolve static analysis CI failures
  ([`4b17679`](https://github.com/oimiragieo/tensor-grep/commit/4b17679e1f9c4ea1d1be8dde4f51f9a6a2bd95c1))

- **rust**: Fix cargo fmt strict linting violations in backend_cpu.rs and lib.rs
  ([`3a00f1c`](https://github.com/oimiragieo/tensor-grep/commit/3a00f1c3e71db1303ea25bf4bae095fbab7b086c))

- **rust**: Fix clippy collapsible_if warnings in backend_cpu.rs
  ([`072fe7d`](https://github.com/oimiragieo/tensor-grep/commit/072fe7d0792be8de536270763aa16d97b1321686))

### Continuous Integration

- Implement exhaustive continuous integration matrix matching ripgrep standards
  ([`bf8ea47`](https://github.com/oimiragieo/tensor-grep/commit/bf8ea4767b57c7a7e4a317844427b178a0fbb6c5))

- Remove UV_SYSTEM_PYTHON flag to prevent externally managed python errors on Debian runner
  ([`9954c2d`](https://github.com/oimiragieo/tensor-grep/commit/9954c2d21723be3b113045d7a5dd25f0b5aea8ef))

### Documentation

- Add formalized release checklist and changelog based on ripgrep protocols
  ([`6ab3d5e`](https://github.com/oimiragieo/tensor-grep/commit/6ab3d5e92a40efad072db845a121cb93d5fbeeb2))

- **readme**: Rewrite README to match ripgrep detailed structure
  ([`38d6f1c`](https://github.com/oimiragieo/tensor-grep/commit/38d6f1c9363b67d90b9ac74766ed15596fdfd48b))

### Features

- **cli**: Intercept top-level sys.argv to automatically forward to search subcommand for perfect
  ripgrep drop-in compatibility
  ([`62db8fc`](https://github.com/oimiragieo/tensor-grep/commit/62db8fc4b196dd11e2d015bce9fbebef34b94e94))

### Testing

- **python**: Fix cybert classification label expectation to include 'warn' from the mock model
  output
  ([`fbcfe71`](https://github.com/oimiragieo/tensor-grep/commit/fbcfe71310398e3e20bd8c4c6e4854e19c361ec1))


## v0.1.3 (2026-02-26)

### Bug Fixes

- **release**: Correct Nuitka binary output filenames for Linux and macOS
  ([`1c65b20`](https://github.com/oimiragieo/tensor-grep/commit/1c65b206eb60ba0639112667a2fef733079dc84c))


## v0.1.2 (2026-02-26)

### Chores

- Bump version to 0.1.2 to trigger Github Actions Nuitka standalone release builds
  ([`8ec5b06`](https://github.com/oimiragieo/tensor-grep/commit/8ec5b06e8584a3b832a63fd38552cc1ec68c0989))

- **deps**: Bump protobuf in the uv group across 1 directory
  ([`8c5fd1c`](https://github.com/oimiragieo/tensor-grep/commit/8c5fd1caeca04820b500fbbfa49188dc537a7ed2))

Bumps the uv group with 1 update in the / directory:
  [protobuf](https://github.com/protocolbuffers/protobuf).

Updates `protobuf` from 4.25.8 to 5.29.6 - [Release
  notes](https://github.com/protocolbuffers/protobuf/releases) -
  [Commits](https://github.com/protocolbuffers/protobuf/commits)

--- updated-dependencies: - dependency-name: protobuf dependency-version: 5.29.6

dependency-type: indirect

dependency-group: uv ...

Signed-off-by: dependabot[bot] <support@github.com>

- **deps**: Bump pyo3
  ([`7914fd2`](https://github.com/oimiragieo/tensor-grep/commit/7914fd2b6cbd4dfa0c1274ed263164097553df25))

Bumps the cargo group with 1 update in the /rust_core directory:
  [pyo3](https://github.com/pyo3/pyo3).

Updates `pyo3` from 0.23.5 to 0.24.1 - [Release notes](https://github.com/pyo3/pyo3/releases) -
  [Changelog](https://github.com/PyO3/pyo3/blob/main/CHANGELOG.md) -
  [Commits](https://github.com/pyo3/pyo3/compare/v0.23.5...v0.24.1)

--- updated-dependencies: - dependency-name: pyo3 dependency-version: 0.24.1

dependency-type: direct:production

dependency-group: cargo ...

Signed-off-by: dependabot[bot] <support@github.com>

- **deps**: Bump tar
  ([`266a725`](https://github.com/oimiragieo/tensor-grep/commit/266a72541ac677fb49b96fe2ad125d3403428947))

Bumps the npm_and_yarn group with 1 update in the /npm directory:
  [tar](https://github.com/isaacs/node-tar).

Updates `tar` from 6.2.1 to 7.5.9 - [Release notes](https://github.com/isaacs/node-tar/releases) -
  [Changelog](https://github.com/isaacs/node-tar/blob/main/CHANGELOG.md) -
  [Commits](https://github.com/isaacs/node-tar/compare/v6.2.1...v7.5.9)

--- updated-dependencies: - dependency-name: tar dependency-version: 7.5.9

dependency-type: direct:production

dependency-group: npm_and_yarn ...

Signed-off-by: dependabot[bot] <support@github.com>

### Documentation

- Add academic paper detailing tensor-grep architecture and arXiv research
  ([`1daa912`](https://github.com/oimiragieo/tensor-grep/commit/1daa91251a2a41f68ec779a620348a7c2fd4fb85))

- Add mermaid graphs and data tables for benchmark visualization
  ([`d6d8e86`](https://github.com/oimiragieo/tensor-grep/commit/d6d8e8687b4cbe5494d1c5b27cf721fe89a1208d))

- Add related work section citing 2025 GPU and AST research to highlight routing novelty
  ([`7e5cd55`](https://github.com/oimiragieo/tensor-grep/commit/7e5cd55f85b1d4d29082e7e8965068408d2dc2e2))

- Add tensor-grep logo to README header
  ([`4620032`](https://github.com/oimiragieo/tensor-grep/commit/4620032fe7d78cc84bb7d0c1e058bb344d7c642e))

- Add WSL Rust memmap benchmark results comparing native Windows execution vs 9P protocol overhead
  ([`26821d9`](https://github.com/oimiragieo/tensor-grep/commit/26821d9b46a4803d51757ede1860861f016863b4))

- Benchmark and fix AST/cyBERT GPU backends
  ([`d37e4f4`](https://github.com/oimiragieo/tensor-grep/commit/d37e4f496257a2471d4b0dc92f8b0a9d2b42a770))

- Clarify CPU vs GPU benchmarks with explicit timings and PCIe overhead mechanics
  ([`2b47a51`](https://github.com/oimiragieo/tensor-grep/commit/2b47a51e3a032ef2754ebe1e43bfb4ccfa6a1d50))

- Expand paper to detail 3rd-party codebases, DFA state explosion, and 4x speedup mechanics
  ([`6b0ca3c`](https://github.com/oimiragieo/tensor-grep/commit/6b0ca3cea079432e2deb31e0dab7c98a2e91bdea))

- Finalized academic paper with RTX 5070 empirical data and ruff linting
  ([`81c0e62`](https://github.com/oimiragieo/tensor-grep/commit/81c0e62bf51afadcb934dbddb075e39446639407))

- Formalize paper abstract and introduce cybersecurity payload deobfuscation
  ([`22a9cb5`](https://github.com/oimiragieo/tensor-grep/commit/22a9cb51fd91398621605bf7e61cea4fef163a5b))

- Include official tensor-grep project logo
  ([`55ecc1b`](https://github.com/oimiragieo/tensor-grep/commit/55ecc1bcbe03215cd0ea3549b07603e24b5592be))

- Update academic paper with PyO3 Rust Native Extension benchmark results
  ([`eb7007d`](https://github.com/oimiragieo/tensor-grep/commit/eb7007d9f88889b75af9a37f747781293cb358c5))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- Update paper with benchmark methodology, 87 tests, and Windows spawn vs WSL2 fork analysis
  ([`6ad6458`](https://github.com/oimiragieo/tensor-grep/commit/6ad6458b4e66a826013c7d4a18aa65825b92fdad))

- Update paper with WSL fork() NVIDIA driver bug and Rust paradigm pivot
  ([`2663ace`](https://github.com/oimiragieo/tensor-grep/commit/2663ace41c1384b8dec05b6e433339832d620533))

### Features

- Scalable GPU deployment matrix and intelligent UV installers
  ([`4d55fff`](https://github.com/oimiragieo/tensor-grep/commit/4d55fff778765c58f2e45da981af55f41c150806))

- **rust**: Implement rayon par_split + memchr fast-path for counting
  ([`02cdc7a`](https://github.com/oimiragieo/tensor-grep/commit/02cdc7a7cdbafa5a2453340e61370b1ebc87ce9c))

Achieved 2x speedup over ripgrep (0.080s vs 0.151s) by leveraging hyper-threaded parallel counting
  inside the Rust PyO3 native extension, fully bypassing Python GIL mapping allocations for -c
  operations.

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

### Refactoring

- Restructure internal codebase architecture for enterprise modularity
  ([`2d08daa`](https://github.com/oimiragieo/tensor-grep/commit/2d08daaa4c3211309c3485947677f141b0ef2a38))


## v0.1.1 (2026-02-25)

### Bug Fixes

- **benchmark**: Optimize PyTorch backend multiprocessing bypass and resolve ripgrep parity issues
  ([`5b28958`](https://github.com/oimiragieo/tensor-grep/commit/5b28958af1bb0a707d644454c02bb79d42692e33))

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>

- **gpu**: Resolve FrozenInstance dataclass update inside TorchBackend worker futures
  ([`06e0012`](https://github.com/oimiragieo/tensor-grep/commit/06e00127e0c847f661a4a308d63d1c5a2413c6bd))

- **gpu**: Resolve Match import and DeviceDetector attribute errors in multi-GPU fallback
  ([`463522c`](https://github.com/oimiragieo/tensor-grep/commit/463522c5ddd077ec37bd81c98f923d334a4619de))

### Chores

- Bump version to 0.1.1
  ([`a2c44e8`](https://github.com/oimiragieo/tensor-grep/commit/a2c44e86fc8446330d8c08bf6626edf9798506a1))

- Rename default branch to main and update references
  ([`879b932`](https://github.com/oimiragieo/tensor-grep/commit/879b9324930e7bab641501ffe9d57336f9772bbb))

- Rename project to tensor-grep and CLI command to tg
  ([`2c9ad46`](https://github.com/oimiragieo/tensor-grep/commit/2c9ad462dc5b45ddb95d3abe9be2939378458817))

### Code Style

- Fix remaining manual ruff linting errors
  ([`19d98da`](https://github.com/oimiragieo/tensor-grep/commit/19d98da1c597a8435b9ffbe5a1d9a58d5ba689e3))

### Continuous Integration

- Fix build_binaries.py path in release workflow
  ([`0e52712`](https://github.com/oimiragieo/tensor-grep/commit/0e52712271ec4d13257d18f6631060ecd941dff3))

### Documentation

- Add GPU requirements and benchmark scripts to README
  ([`5d1bdf5`](https://github.com/oimiragieo/tensor-grep/commit/5d1bdf5473ad76db7459ffc6028621eada228925))

- Add initial README.md
  ([`b5ea6fb`](https://github.com/oimiragieo/tensor-grep/commit/b5ea6fbfab297eae32b7ea2043e3d7ec7d8847ae))

- Add Multi-GPU workload distribution to release plan
  ([`16a04bd`](https://github.com/oimiragieo/tensor-grep/commit/16a04bdbb896bda21755b8769d0f857a0078bfda))

- Add package manager manifests
  ([`8344891`](https://github.com/oimiragieo/tensor-grep/commit/834489144878786ad813a06d62204f0a8032d886))

- Add PyPI installation instructions
  ([`df639fa`](https://github.com/oimiragieo/tensor-grep/commit/df639fadf63abc152e1cac7a77b4018743b8b023))

- Add V1.0.0 TDD Release Plan
  ([`4f45c75`](https://github.com/oimiragieo/tensor-grep/commit/4f45c75d9b789700e93828d030559cb313781953))

- Document hardware and software requirements
  ([`e07b348`](https://github.com/oimiragieo/tensor-grep/commit/e07b348f47e86f0599a78a660b6a57bce576013a))

- Note Python version requirement for Windows PyTorch
  ([`1ec2e7f`](https://github.com/oimiragieo/tensor-grep/commit/1ec2e7fb76cf43dc2d3dcd5f19b9c5d99b68aee2))

- Update benchmark scores and uv instructions for native Windows GPUs
  ([`3555335`](https://github.com/oimiragieo/tensor-grep/commit/355533543c4d49cef4e11179cd0bc6d90e4b6373))

### Features

- **ast**: Implement PyTorch Geometric GNN backend for structural AST-Grep matching
  ([`3a1649c`](https://github.com/oimiragieo/tensor-grep/commit/3a1649cd29801978f61a1dbe2ef21ef1cfee19fb))

- **cli**: Add full ast-grep CLI command parity (run, scan, test, new, lsp)
  ([`8dd71dc`](https://github.com/oimiragieo/tensor-grep/commit/8dd71dc9234814e36466fecfd93cf5f6a1c76cf0))

- **cli**: Implement full ripgrep argument flag parity
  ([`e1014d7`](https://github.com/oimiragieo/tensor-grep/commit/e1014d7dbc3fbaeade19b85c3c4fe43a8a3e199b))

- **core**: Implement Phase 1 and 2 TDD features including cyBERT flags, context lines, and file
  traversal
  ([`829da77`](https://github.com/oimiragieo/tensor-grep/commit/829da77bf775a147bc4001d9f2a0fd998e067c56))

- **core**: Wire ripgrep parity flags to SearchConfig and implement in CPUBackend
  ([`694f4e2`](https://github.com/oimiragieo/tensor-grep/commit/694f4e2316136257f26e311f9f7575519fcf596a))

- **enterprise**: Setup Nuitka standalone binary compiler, mkdocs material docs, and npx wrapper
  ([`485dd3f`](https://github.com/oimiragieo/tensor-grep/commit/485dd3f20fc6cd1649e3572e39a4f738a87d2da0))

- **gpu**: Add native Windows PyTorch CUDA fallback backend
  ([`305b471`](https://github.com/oimiragieo/tensor-grep/commit/305b471a0853f531fd8bd9ad319588bc885e8e20))

- **gpu**: Distribute workloads across multiple GPUs using ProcessPoolExecutor
  ([`817ac73`](https://github.com/oimiragieo/tensor-grep/commit/817ac73e4573b46b4dad61475eb51f39d78ba87b))

- **gpu**: Scale native Windows PyTorch backend to multi-GPU arrays
  ([`b5827e6`](https://github.com/oimiragieo/tensor-grep/commit/b5827e6776ea3649fe8ee850402b11824fba4b0d))

### Refactoring

- Enterprise-grade Python 2026 structure
  ([`0e57897`](https://github.com/oimiragieo/tensor-grep/commit/0e57897ee1bbc49cd24d026d85e2e73efd25c1be))

- Consolidated 8 test folders into 3 standard tiers (unit, integration, e2e) - Cleaned root
  directory by moving benchmarks, scripts, and planning docs - Migrated tooling to uv and ruff with
  comprehensive linting rules - Fixed 10,000+ linting issues across the codebase - Restored
  CPUBackend context capturing logic - Rebuilt pytest snapshots

Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>


## v0.1.0 (2026-02-25)
