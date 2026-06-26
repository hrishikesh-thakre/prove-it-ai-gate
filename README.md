# prove-it-ai-gate

**Don't trust agent output. Accept evidence.**

[![CI](https://github.com/hrishikesh-thakre/prove-it-ai-gate/actions/workflows/tests.yml/badge.svg)](https://github.com/hrishikesh-thakre/prove-it-ai-gate/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/prove-it-ai-gate)](https://pypi.org/project/prove-it-ai-gate/)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

A lightweight local CLI acceptance gate for AI-generated engineering work. Checks agent transcripts, evidence folders, workspace state, and validation outputs before accepting AI-generated work.

> **Warning:** This tool checks evidence quality, not absolute correctness. It is not a substitute for human review on high-risk changes.

## Project Status

`prove-it-ai-gate` is currently an **early beta**.

**Stable core:**
- `transcript-check`
- `workspace-check`
- `evidence-check`
- `accept`

**Experimental:**
- `opencode-watch` (Windows Desktop live watcher)
- `reuse-scan`
- `capture`
- `wiki upload`
- `evidence cleanup/archive`

Use the stable core for local dogfooding. Treat experimental features as evolving APIs.

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

# Scan a knowledge wiki before starting work (experimental)
ai-gate reuse-scan --brief brief.md --local-wiki ./wiki-exports

# Capture reusable learnings after acceptance (experimental)
ai-gate capture --evidence ./evidence --output ./learnings
```

## OpenCode Desktop Watcher (Experimental)

Live background guard for OpenCode Desktop on Windows. Polls the OpenCode SQLite
database for new tool events, detects high-signal violations in near-real-time,
raises Windows toast alerts, and runs final acceptance at session end.

```bash
# Start watching an OpenCode session
ai-gate opencode-watch --project-dir . --task-type audit

# With auto-terminate on hard violations
ai-gate opencode-watch --project-dir . --task-type audit --auto-terminate
```

**How it works:**
- Polls `~/.local/share/opencode/opencode.db` every 2s for new tool events
- Detects secret file reads, blocker commands (`rm -rf /`, `DROP TABLE`, `curl | sh`),
  and warning-tier commands (`chmod 777`, `git push --force`)
- Persists evidence to `evidence/transcript.jsonl`, `live_watch_report.jsonl`,
  `workspace/git_status_after.txt`, and `closeout.md`
- Raises Windows toast alerts on violations
- Optionally terminates OpenCode on hard violations (`--auto-terminate`)
- Runs `ai-gate accept` at session end (Ctrl+C or natural session close)

**Limitations:**
- SQLite-based — near-real-time, not pre-tool-call blocking
- Windows Desktop only; CLI/TUI plugin path is separate experimental track
- `--auto-terminate` is opt-in; only kills OpenCode-tagged processes

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

## Dogfood

Run automated dogfood cases to verify the gate catches known failure patterns:

```bash
python scripts/run_dogfood.py
```

See `dogfood/dogfood_report.md` for results and `examples/dogfood/` for test case fixtures.

## Policy Packs

Three built-in policies in `prove_it_ai_gate/policies/`:

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
- [Release Readiness](docs/release-readiness.md)

## License

MIT — see [LICENSE](LICENSE).
