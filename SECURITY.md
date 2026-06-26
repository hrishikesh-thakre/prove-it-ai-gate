# Security

`prove-it-ai-gate` is an acceptance gate for AI-generated engineering work. It checks evidence quality, not security correctness.

## Reporting a Vulnerability

If you discover a security vulnerability in `prove-it-ai-gate` itself (e.g., the tool can be bypassed in a way that produces unsafe acceptances), please report it privately.

Open a GitHub issue marked **confidential** or contact the maintainer directly.

## What This Tool Does NOT Protect Against

- **Malicious agents**: The gate checks evidence, but a sufficiently sophisticated agent can fabricate evidence that passes all checks while the underlying work is wrong. This is a fundamental limitation of evidence-based gates.
- **Supply chain attacks**: The gate does not audit dependencies, build artifacts, or deployment pipelines.
- **Secrets in evidence**: Evidence folders may contain sensitive data (file paths, code snippets). Users are responsible for redacting secrets before committing evidence to version control or sharing publicly.
- **Network security**: The Reuse Scout feature makes HTTP requests to wiki APIs. Use trusted endpoints only.

## Safe Usage

- Do not use this tool as the sole gate for production deployments, security-critical changes, or compliance sign-offs.
- Always require human review for high-risk tasks (auth, crypto, data migration, production deployment).
- Redact secrets from evidence folders before archival or public sharing.
- Keep the tool updated. Early beta releases may have gaps in check coverage.

## Dependencies

- `pyyaml>=6.0` — YAML parsing for policy packs
- Standard library only otherwise (no network deps except Reuse Scout which uses `urllib`)

All dependencies are pure Python and have no compiled extensions.
