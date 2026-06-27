# Prove It Without Me

The core principle behind `prove-it-ai-gate`.

## The Problem

AI agents are increasingly capable, but their outputs are not consistently trustworthy for serious engineering work. Common failure modes:

- Agent ignores explicit instructions
- Agent analyzes truncated or partial data
- Agent treats a guard pass as proof the conclusion is correct
- Agent uses weak heuristics and claims completeness
- Agent overstates confidence
- Agent says "no files changed" even when files were edited
- Agent produces polished but unsupported sign-off claims

The bottleneck has moved from **generation** to **acceptance**.

## The Principle

**AI-generated work is not accepted because the model says it is correct.**

It is accepted only when deterministic evidence supports the claim.

The system treats model output as a proposal, not proof.

Acceptance depends on:

- Evidence artifacts (not prose claims)
- Reproducible commands
- Workspace state
- Validation results
- Risk-tiered policy checks
- Confidence discipline
- Explicit unresolved risks
- Human approval for high-risk work

## Automated Evidence

The preferred implementation path is a supervisor daemon that owns the capture
boundary:

- register the project once
- capture workspace state before a supported session when possible
- capture tool events through supported adapters
- capture final workspace state and diff summary
- run conservative validation
- write an acceptance report

This removes the adoption blocker of asking the user to remember manual
before/after commands for every session.

Automated evidence is still truthful evidence, not forced acceptance:

- late capture is marked with `late_snapshot=true`
- transcript reconstruction is marked `FALLBACK_ONLY`
- missing validation blocks code-change acceptance
- failed validation rejects code-change acceptance
- untrusted/unobserved Codex hooks are reported as unverified coverage

## What This Is Not

This is not proof that the work is absolutely correct. The tool checks evidence quality, not absolute truth.

A passing acceptance report means: the evidence is sufficient, the checks passed, and the work can proceed. It does not mean the work is bug-free or mathematically correct.

## The Tagline

> Don't trust agent output. Accept evidence.
