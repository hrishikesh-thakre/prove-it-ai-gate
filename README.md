# prove-it-ai-gate

**Don't trust agent output. Accept evidence.**

A lightweight local CLI acceptance gate for AI-generated engineering work.

`prove-it-ai-gate` checks agent transcripts, evidence folders, workspace state, and validation outputs before accepting AI-generated engineering work.

## Installation

```bash
pip install -e .
```

Requires Python 3.9+ and `pyyaml`.

## Quick Start

```bash
# Initialize a project
ai-gate init

# Check an agent transcript for truncation
ai-gate transcript-check --transcript transcript.jsonl

# Check workspace hygiene
ai-gate workspace-check --repo ./target-project

# Check evidence folder completeness
ai-gate evidence-check --evidence ./evidence --task-type audit

# Run the full acceptance gate
ai-gate accept \
  --repo ./target-project \
  --evidence ./evidence \
  --transcript transcript.jsonl \
  --task-type audit \
  --policy read_only_audit
```

## Scan a Knowledge Wiki Before Work Starts

```bash
# Scan a local wiki export for reusable assets
ai-gate reuse-scan --brief brief.md --local-wiki ./wiki-exports

# Or connect to a live wiki API
ai-gate reuse-scan --brief brief.md --wiki-url http://localhost:6875
```

## Decision States

| Decision | Meaning |
|---|---|
| `ACCEPT` | Required evidence exists and checks passed |
| `ACCEPT_WITH_CONDITIONS` | Mostly acceptable, but follow-ups remain |
| `REJECT` | Evidence proves a failure or unacceptable risk |
| `BLOCKED` | Required evidence is missing or invalid |

## Built-in Policy Packs

- `read_only_audit.yml` — Audits, reviews, listings, evaluations, signoffs
- `code_change.yml` — Code modifications with tests and validation
- `corpus_audit.yml` — Full-corpus scans with extraction artifacts

## Core Checks

| Check | What it catches |
|---|---|
| Transcript truncation | Agent used truncated tool output without replacement evidence |
| Workspace hygiene | Agent modified target repo during read-only task, or claimed no changes when git shows otherwise |
| Evidence folder schema | Missing, empty, or placeholder evidence files |
| Confidence claims | Unsupported high-confidence claims, heuristic-extraction overclaim |
| Scope completeness | Bare "Complete" claims without scoped corpus definition |
| Heuristic extraction | Filename/keyword matching claimed as full coverage |
| Closeout vs transcript | Closeout claims "no files changed" but transcript shows write/create/edit events |

## Evidence Folder Contract

Every agent task should produce:

```
evidence/
  brief.md
  plan.md
  commands.jsonl
  changed_files.txt
  closeout.md
  risks.md
  validation/
    test_output.txt
    lint_output.txt
    typecheck_output.txt
    audit_guard.txt
  artifacts/
    inventory.json
    extracted_findings.json
  workspace/
    git_status_before.txt
    git_status_after.txt
    diff_summary.patch
  transcript.jsonl
```

See `docs/evidence-folder-contract.md` for required files by task type.

## Configuring Thresholds

Confidence thresholds are configured per policy pack:

```yaml
confidence_thresholds:
  high: 0.90       # above this requires deterministic evidence
  heuristic_cap: 0.75   # heuristic extraction cannot exceed this
```

## License

MIT
