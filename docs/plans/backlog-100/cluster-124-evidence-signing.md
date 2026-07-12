# Build-Ready Spec — Task #124: EvidenceReceipt Signing Layer (P2/P3)
# (Opus design audit 2026-07-12; build when a WIP slot opens -> Sonnet TDD -> mandatory Opus gate crypto)

## HEADLINE
- NOT greenfield. Field names already reserved: evidence_receipt.py:18-19 (receipt_sha256 / signature / previous_receipt_sha256 + `tg evidence verify`). Use exactly those.
- In-house signing precedent to PORT (not reinvent): audit_manifest.py (HMAC-SHA256 over canonical bytes, out-of-band key, S2 "never trust an embedded key", fail-closed).
- PYTHON-ONLY: `evidence` is KNOWN_COMMAND (commands.py:43), PUBLIC_TOP_LEVEL, native forwards via handle_python_passthrough (rust_core/src/main.rs:4554, disable_help_flag=true :1103). New subcommands (verify/keygen/pubkey) + emit flags flow through the passthrough into Typer evidence_app (main.py:273, emit at :13489). NO Rust rebuild, NO 4-site registration, NO native-asset push-race.

## THE DECISION: Ed25519 (recommend) vs HMAC (BACKLOG:94 assumed)
- Recommend **Ed25519**: gotcontext is a SEPARATE consumer (#99 directive) = cross-trust-boundary producer->consumer = asymmetric (verify-without-forge). audit_manifest uses HMAC correctly because it is LOCAL tamper-evidence (same operator signs+verifies).
- ZERO dep cost: cryptography==49.0.0 already resolved (uv.lock:560) transitively via mcp>=1.2.0 -> pyjwt[crypto] -> cryptography (uv.lock:2076,3430). Add `cryptography>=48.0.1` to [project].dependencies (pyproject:499; already constrained [tool.uv]:24).
- Wire format is algorithm-agnostic (signing.algorithm) so HMAC secondary can be added later (reuse audit_manifest hmac.new + compare_digest, :803-808) with no format break.

## CANONICAL SERIALIZATION: `tg-canonical-json-v1`
compact sorted (precedent audit_manifest.py:65 _canonical_json_bytes), NOT the indent=2 form (that exists only to byte-match the Rust writer; evidence has no Rust writer).
```python
def canonical_receipt_bytes(receipt: dict) -> bytes:
    canonical = dict(receipt)
    canonical.pop("signature", None)        # excluded: added after signing
    canonical.pop("receipt_sha256", None)   # excluded: digest of these very bytes
    return json.dumps(canonical, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")
```
Everything EXCEPT signature + receipt_sha256 is signed (incl the whole `signing` block -> algorithm claim authenticated -> blocks downgrade). Residual: float repr is Python-json-specific -> gotcontext must match number serializer; keep receipt floats simple (they are).

## WIRE FORMAT
Unsigned (default): existing blocks + `"receipt_sha256": "<hex>"` (keyless integrity/dedup/chain, always present).
Signed (--sign): adds `"signing": {algorithm:"ed25519", key_id:"sha256:<hex>", public_key:"<b64 raw 32B>", signed_at, canonicalization:"tg-canonical-json-v1", receipt_id:"<uuid4>"}`, optional `"previous_receipt_sha256"`, `"receipt_sha256"`, `"signature": {"value":"<b64 sig>"}`. signature block holds ONLY the raw value (identity lives in the signed signing block).

## KEY MANAGEMENT
Priv key precedence: --signing-key <path> ; TG_EVIDENCE_SIGNING_KEY (path) ; default ~/.tensor-grep/keys/evidence_ed25519.key (per-USER home, NEVER the per-repo .tensor-grep/ that audit_manifest writes to).
`tg evidence keygen`: Ed25519 keypair, priv at 0o600 via O_CREAT|O_EXCL|mode atomic (session_store._write_json_atomic pattern :403-436), pub .pub 0o644, prints pubkey+key_id, refuses overwrite (O_EXCL) unless --force.
`tg evidence pubkey`: prints b64 pubkey + key_id for gotcontext registration.
NO-KEY FAIL CLOSED (AGENTS.md:272-282): emit --sign with no key -> non-zero + actionable error, NEVER emit unsigned while --sign requested (the --pcre2 anti-pattern). Unsigned only when --sign absent.

## CLI/MCP SURFACE (all Python-only, hook points cited)
- emit: extend evidence_emit main.py:13489-13568, add --sign/--signing-key/--previous; thread into build_evidence_receipt (evidence_receipt.py:708).
- verify: new @evidence_app.command("verify") after main.py:13588; reports digest_valid, signature_valid, key_id/fingerprint, key_trusted; flags --trusted-key... --require-trusted --previous --json.
- keygen/pubkey: new @evidence_app.commands same group.
- P3 (separate PR): tg_evidence_verify MCP tool (verify-only, NO secret read) at mcp_server.py:150-164; if signing ever over MCP, gate priv-key read behind TG_MCP_ALLOW_EVIDENCE_SIGNING_KEY_READ=1 (mirror :5081).

## ADVERSARIAL SECURITY (crypto load-bearing)
1. Canonicalization ambiguity -> mitigated by sort_keys+compact+ensure_ascii+allow_nan=False+utf8; algorithm inside signed bytes (no downgrade). Residual: float serializer parity.
2. Key-file perm window -> O_WRONLY|O_CREAT|O_EXCL,0o600 -> write -> fsync -> atomic replace; NEVER write_text-then-chmod (AGENTS.md:300). O_EXCL refuses pre-existing file/symlink.
3. **S2 trust-bootstrap (highest-value):** embedded pubkey != authenticity (attacker signs w/ own key + embeds own pubkey). verify MUST (a) always report key fingerprint, (b) with a trusted-key set (--trusted-key/--trusted-keys-dir/TG_EVIDENCE_TRUSTED_KEYS) fail closed unless embedded pubkey matches a pinned key (hmac.compare_digest on fingerprint); --require-trusted -> unpinned = valid:False. Restatement of audit_manifest.py:791-796. Symmetric: --trusted-key vs UNSIGNED receipt -> invalid (audit_manifest.py:813-815).
4. Replay: sigs don't expire -> DETECTABLE not prevented: receipt_id(uuid4)+signed_at+repo-binding(revision.commit_sha+dirty_tree_sha256 :206-214) -> gotcontext dedupe/staleness/context-bind (CONSUMER responsibility).
5. Tampered field -> canonical bytes change -> signature_valid=False AND receipt_sha256 mismatch -> non-zero.
6. No secret in logs: errors reference key PATH not contents; keygen/pubkey print only public; MCP signing-key read gated OFF.
7. Timing: cryptography Ed25519 verify constant-time; hmac.compare_digest for fingerprint/HMAC.
8. Missing-crypto fail-closed: guard the import; --sign w/ lib absent -> hard error, never unsigned.
9. Verify-path DoS: bound the read (reject > a few MB) before json.loads (AGENTS.md:299).

## BUILD PLAN
NEW src/tensor_grep/cli/evidence_signing.py (isolate all crypto). Functions: canonical_receipt_bytes, receipt_digest, generate_keypair(out_path,*,force=False), resolve_signing_key_path(flag), load_private_key(path), public_key_b64, key_id_from_public_b64, sign_receipt(receipt,*,private_key_path,previous_receipt_sha256=None), verify_receipt(receipt,*,trusted_public_keys=None,require_trusted=False)->{valid,checks:{digest_valid,signature_valid,key_trusted},key_id,algorithm,trust,errors}.
MODIFY evidence_receipt.py: add sign/signing_key_path/previous_receipt_path params to build_evidence_receipt(:708) + build_evidence_receipt_json(:802); attach receipt_sha256 ALWAYS after assembly (:799), signing+signature under sign=True, delegate to evidence_signing.
MODIFY main.py: --sign/--signing-key/--previous on evidence_emit(:13489) + fail-closed wiring; add verify/keygen/pubkey commands after :13588. NO clap/native changes.
MODIFY pyproject.toml: add cryptography>=48.0.1 to [project].dependencies:499. Governance: test_pyproject_dependencies pins only optional-deps groups+ruff (:12-49) -> core dep add does not trip it; confirm.
NEW tests/unit/test_evidence_signing.py (13 tests below). MODIFY test_evidence_receipt.py (new receipt_sha256 is additive; update any full-dict snapshot).
DOCS: docs/CONTRACTS.md (wire-format + tg-canonical-json-v1 rule; test_public_docs_governance may pin), AGENTS.md one-liner, tg skill REFERENCE.md, CHANGELOG.md.

## TDD TESTS (bidirectional oracle)
1 sign->verify roundtrip. 2 tampered-rejected (flip byte -> signature_valid=False, non-zero). 3 no-key fail-closed (--sign no key -> non-zero, NO receipt written; unsigned default still emits). 4 canonical-determinism (shuffled dict order -> identical bytes+sig). 5 cross-process verify (embedded pubkey + pinned --trusted-key). 6 untrusted-key detected (attacker re-signs -> signature_valid=True but key_trusted=False; --require-trusted -> valid=False) [the S2 test]. 7 wrong trusted key. 8 key-file perms 0o600 + O_EXCL refuses overwrite + symlink refused. 9 digest-only unsigned verifiable. 10 chain previous_receipt_sha256 match/mismatch. 11 missing-crypto fail-closed (monkeypatch import). 12 verify DoS guard (oversized file bounded). 13 real-binary dogfood e2e (bootstrap front door NOT CliRunner: keygen->emit --sign->verify; ASCII-only).

## GATES
4-gate (ruff format --preview + check ; mypy ; pytest incl test_evidence_signing + dogfood shipped binary test#13 ; governance test_pyproject_dependencies + test_public_docs_governance). MANDATORY Opus gate at build (crypto load-bearing): adversarial file:line review focused on canonicalization/key-perms/trust-bootstrap. Optional 2nd lens codex -m gpt-5.6-sol.

## CEO-DECISION POINTS (defaults chosen; DO NOT block the build)
1 Ed25519 vs HMAC trust model (default Ed25519; confirm if gotcontext ever same-trust-domain -> HMAC secondary ~40 lines). 2 key scope per-user (default) vs per-org. 3 does gotcontext ever accept unsigned (tg emits unsigned unless --sign; rejection = gotcontext policy). 4 embedded (default) vs detached .sig sidecar. 5 canonicalization coordination = integration task (hand gotcontext a reference verify snippet + float caveat).

RECOMMENDED FIRST PR (P2): Ed25519 emit --sign + verify + keygen + pubkey, Python-only, Opus-gated. Defer MCP tg_evidence_verify (P3) + HMAC secondary.

---

## Build notes (added post-implementation, 2026-07-12)

The implementation follows this spec with two deliberate, minimal deviations (both noted in the PR
description):

1. `verify_receipt`'s literal spec'd signature (`trusted_public_keys`, `require_trusted`) does not
   take a `previous_receipt_path`/chain parameter, so the `--previous` chain check is a SEPARATE
   function, `verify_receipt_chain(receipt, *, previous_path)`, called by the CLI layer
   (`evidence_receipt.verify_evidence_receipt`) only when `--previous` is passed. This keeps
   `verify_receipt`'s return shape exactly as specified while still shipping the `--previous` CLI
   flag from the surface bullet.
2. `--require-trusted` is the flag that gates `valid` on `key_trusted`; supplying `--trusted-key`
   alone always POPULATES and reports `key_trusted` (the "always report the fingerprint" S2
   requirement) without by itself failing `valid` -- matching the TDD list's own test 6 wording
   ("`--require-trusted` -> valid=False", implied as distinct from the default). The one case that
   fails closed unconditionally (no extra flag needed) is a trusted-key set/`--require-trusted`
   evaluated against an UNSIGNED receipt, mirroring `audit_manifest.py`'s asymmetric
   "signing key supplied but manifest unsigned" rule literally (audit_manifest.py:813-815).

CHANGELOG.md was intentionally NOT hand-edited: it is semantic-release-generated
(`.claude/skills/tensor-grep-docs-and-writing/SKILL.md`), not a hand-pinned doc.
