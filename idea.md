# prove-it-ai-gate: Evidence-First AI Acceptance Gate

## One-Line Idea

**Do not trust agent output. Accept evidence.**

AI agents produce work. `prove-it-ai-gate` decides whether that work is acceptable.

## Problem

AI agents are capable, but their outputs are not consistently trustworthy for serious engineering work.

Observed failure modes:

- Agent ignores explicit instructions
- Agent analyzes truncated or partial data
- Agent treats a guard pass as proof the conclusion is correct
- Agent uses weak heuristics and claims completeness
- Agent overstates confidence
- Agent says "no files changed" even when files were edited
- Agent produces polished but unsupported sign-off claims

The bottleneck has moved from **generation** to **acceptance**.

## Core Principle: Prove It Without Me

AI-generated work is not accepted because the model says it is correct.

It is accepted only when deterministic evidence supports the claim.

## What This Is

A local CLI acceptance gate for AI-assisted engineering work.

It combines:

1. **Acceptance Gate** — Determines ACCEPT / ACCEPT_WITH_CONDITIONS / REJECT / BLOCKED
2. **Evidence Checks** — Transcript truncation, workspace hygiene, confidence claims, scope completeness, heuristic extraction
3. **Policy Packs** — Configurable YAML policies defining required checks per task type
4. **Reuse Scout** — Scans a knowledge wiki for reusable assets, ADRs, patterns, and failure modes before work starts

## What This Is Not

- Another AI coding assistant
- A replacement for human judgment
- A generic linter or CI/CD system
- Proof that the work is absolutely correct

## Quick Start

```bash
pip install -e .
ai-gate init
ai-gate accept --repo . --evidence ./evidence --transcript transcript.jsonl
ai-gate reuse-scan --brief brief.md --local-wiki ./wiki-exports
```

## Policy Packs

| Policy | Use Case |
|---|---|
| `read_only_audit.yml` | Audits, reviews, listings, evaluations |
| `code_change.yml` | Code modifications with tests and validation |
| `corpus_audit.yml` | Full-corpus scans with extraction artifacts |

## Decision States

| Decision | Meaning |
|---|---|
| `ACCEPT` | Required evidence exists and checks passed |
| `ACCEPT_WITH_CONDITIONS` | Mostly acceptable, but follow-ups remain |
| `REJECT` | Evidence proves a failure or unacceptable risk |
| `BLOCKED` | Required evidence is missing or invalid |

## Architecture

```
src/
  cli.py              CLI entrypoint (init, accept, reuse-scan, ...)
  gate_engine.py       Policy resolution, check orchestration, decision model
  types.py             Decision, Severity, Issue, CheckResult data types
  transcript_checker.py     Truncation detection in agent transcripts
  workspace_guard.py        Git status, read-only violation, scratch file detection
  evidence_checker.py       Evidence folder schema validation
  confidence_checker.py     Confidence claim discipline
  scope_completeness_checker.py  Bare-vs-scoped completeness claims
  heuristic_checker.py      Heuristic extraction detection
  transcript_crosscheck.py  Closeout vs transcript write-event cross-check
  acceptance_report.py      JSON, Markdown, CSV report generation
  reuse_scout.py            Wiki API client, keyword scoring, reuse classification
```

## Docs

- `docs/evidence-folder-contract.md` — Required evidence by task type
- `docs/acceptance-policy-model.md` — Decision states, policy structure, confidence rules
- `docs/transcript-schema.md` — JSONL transcript format
- `docs/workspace-hygiene.md` — Git status and repo modification checks
- `docs/confidence-rules.md` — Confidence thresholds and discipline
- `docs/prove-it-without-me.md` — Core design principle

## License

MIT
