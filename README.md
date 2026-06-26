# prove-it-ai-gate

**Don't trust agent output. Accept evidence.**

[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-80%20passed-brightgreen)](tests/)

A lightweight local CLI acceptance gate for AI-generated engineering work. Checks agent transcripts, evidence folders, workspace state, and validation outputs before accepting AI-generated work.

## Installation

```bash
pip install prove-it-ai-gate
```

Or from source:

```bash
git clone https://github.com/hrishikesh-thakre/prove-it-ai-gate.git
cd prove-it-ai-gate
pip install .
```

Requires Python 3.9+.

## Quick Start

```bash
ai-gate init
ai-gate transcript-check --transcript transcript.jsonl
ai-gate workspace-check --repo ./my-project
ai-gate evidence-check --evidence ./evidence --task-type audit

# Full acceptance gate
ai-gate accept \
  --repo ./my-project \
  --evidence ./evidence \
  --transcript transcript.jsonl \
  --task-type audit

# Scan a knowledge wiki before starting work
ai-gate reuse-scan --brief brief.md --local-wiki ./wiki-exports

# Capture reusable learnings after acceptance
ai-gate capture --evidence ./evidence --output ./learnings
```

## Decision States

| Decision | Meaning |
|---|---|
| `ACCEPT` | Required evidence exists and checks passed |
| `ACCEPT_WITH_CONDITIONS` | Mostly acceptable, but follow-ups remain |
| `REJECT` | Evidence proves a failure or unacceptable risk |
| `BLOCKED` | Required evidence is missing or invalid |

## Built-in Checks

| Check | What it catches |
|---|---|
| `transcript_truncation_check` | Agent used truncated tool output without replacement evidence |
| `workspace_hygiene_check` | Agent modified target repo during read-only task, scratch files in repo |
| `evidence_folder_schema_check` | Missing, empty, or placeholder evidence files |
| `confidence_claim_check` | Unsupported high-confidence claims, heuristic overclaim |
| `scope_completeness_check` | Bare "Complete" claims without scoped corpus definition |
| `heuristic_extraction_check` | Filename/keyword matching claimed as full coverage |
| `closeout_transcript_crosscheck` | Closeout claims "no changes" but transcript shows write events |
| `evidence_inventory_integrity` | Inflated inventory counts vs transcript tool outputs |
| `evidence_validation_integrity` | Test/lint output mismatch with closeout claims |
| `evidence_depth` | Shallow/minimal evidence stubs |
| `git_status_authenticity` | Fabricated git status not matching transcript |

## Policy Packs

Three built-in policies in `src/policies/`:

| Policy | Use Case |
|---|---|
| `read_only_audit.yml` | Audits, reviews, listings, evaluations |
| `code_change.yml` | Code modifications with tests and validation |
| `corpus_audit.yml` | Full-corpus scans with extraction artifacts |

## Evidence Folder Contract

Every agent task should produce:

```
evidence/
  brief.md, plan.md, commands.jsonl, changed_files.txt
  closeout.md, risks.md
  validation/test_output.txt, lint_output.txt, typecheck_output.txt, audit_guard.txt
  artifacts/inventory.json, extracted_findings.json
  workspace/git_status_before.txt, git_status_after.txt, diff_summary.patch
  transcript.jsonl
```

See `docs/evidence-folder-contract.md` for requirements by task type.

## Docs

- [Evidence Folder Contract](docs/evidence-folder-contract.md)
- [Acceptance Policy Model](docs/acceptance-policy-model.md)
- [Transcript Schema](docs/transcript-schema.md)
- [Workspace Hygiene](docs/workspace-hygiene.md)
- [Confidence Rules](docs/confidence-rules.md)
- [Prove It Without Me](docs/prove-it-without-me.md)

## License

MIT — see [LICENSE](LICENSE).
