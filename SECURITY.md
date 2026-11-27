# Security Policy

## Security Contact & Reporting A Vulnerability

Report suspected vulnerabilities privately:

- **GitHub Private Security Advisory** (preferred): `https://github.com/oimiragieo/tensor-grep/security/advisories/new`
- **Security Contact Email**: `security@tensor-grep.dev`

When reporting:

- include the affected version (`tg --version`)
- include reproduction steps or a proof of concept when safe
- describe whether the issue affects release binaries, Python packaging, MCP/LSP tooling, or package-manager installers

Do not open a public issue for undisclosed vulnerabilities.

Public issue triage is automated and intentionally conservative. If a public issue appears security-sensitive, or the reporter does not confirm that it contains no undisclosed vulnerability, exploit, secret, credential, or private token, the triage workflow labels it for private security review and points the reporter to the private advisory flow. The workflow does not execute issue content or send it to external AI services.

## Response SLA

| Severity | Initial Acknowledgment | Triage & Reproduction | Target Remediation |
| --- | --- | --- | --- |
| Critical (RCE, supply-chain, credential leak) | <= 24 hours | <= 48 hours | <= 7 days |
| High (privilege escalation, path traversal) | <= 24 hours | <= 72 hours | <= 14 days |
| Medium / Low | <= 48 hours | <= 5 business days | <= 30 days |

## Supported Versions

| Version | Supported | Notes |
| --- | --- | --- |
| `1.142.x` (current `1.x` line) | Yes | Active security patches, SLSA Level 3 provenance, Sigstore signing |
| `< 1.0` | No (EOL) | Upgrade to latest `1.x` release |

## Zero-Telemetry Guarantee

`tensor-grep` is strictly local-first and ships with **zero telemetry**:

- No analytics, usage beacons, crash-reporting phone-home, or license-check network calls during search, AST indexing, or MCP server execution.
- Outbound network connections occur **only** when you explicitly invoke an installer or package manager (`install.sh`, `install.ps1`, `pip`, `uv`, `brew`, `winget`, `cargo`).

## Supply-Chain & Air-Gapped Verification Guide

Every tagged release (`vX.Y.Z`) publishes:

1. **SHA-256 Checksums**: `CHECKSUMS.txt` covering all native binaries and SBOMs.
2. **Sigstore / Cosign Keyless Bundles**: `*.sigstore.json` signed by GitHub Actions OIDC (`https://token.actions.githubusercontent.com`).
3. **SLSA Level 3 Build Provenance**: GitHub Artifact Attestations via `actions/attest-build-provenance`.
4. **CycloneDX SBOMs**: `sbom-rust.json` (Rust crate graph) and `sbom-python.json` (locked Python dependency graph).

### Online / Standard Verification

```bash
# 1. Verify SHA-256 checksum
sha256sum -c CHECKSUMS.txt --ignore-missing

# 2. Verify Cosign Sigstore bundle
cosign verify-blob \
  --bundle tg-linux-amd64-cpu.sigstore.json \
  --certificate-identity "https://github.com/oimiragieo/tensor-grep/.github/workflows/ci.yml@refs/heads/main" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  tg-linux-amd64-cpu

# 3. Verify SLSA Level 3 provenance attestation
gh attestation verify tg-linux-amd64-cpu --repo oimiragieo/tensor-grep
```

### Air-Gapped Verification

1. Transfer the release binary, `CHECKSUMS.txt`, `*.sigstore.json`, `sbom-rust.json`, `sbom-python.json`, and the offline `gh attestation verify --bundle <attestation.jsonl>` bundle into the air-gapped enclave.
2. Verify the binary digest against `CHECKSUMS.txt` (`sha256sum -c` or PowerShell `Get-FileHash -Algorithm SHA256`).
3. Verify the Sigstore bundle offline using `--offline` and a cached TUF root:
   ```bash
   cosign verify-blob --offline --bundle tg-linux-amd64-cpu.sigstore.json \
     --certificate-identity "https://github.com/oimiragieo/tensor-grep/.github/workflows/ci.yml@refs/heads/main" \
     --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
     tg-linux-amd64-cpu
   ```
4. Inspect `sbom-rust.json` and `sbom-python.json` with your organization's CycloneDX policy scanner before admitting the binary.
