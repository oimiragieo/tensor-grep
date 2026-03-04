# CHANGELOG


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
