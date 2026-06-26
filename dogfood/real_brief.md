# Project Brief: Build an AI Agent Acceptance Gate

Build a local CLI tool that checks whether AI-generated engineering work
has enough deterministic evidence to be accepted before human sign-off.

## Requirements
- Catch truncated tool output used without replacement
- Detect read-only audit repo pollution
- Flag unsupported confidence claims
- Identify heuristic extraction claimed as complete
- Produce deterministic acceptance reports (ACCEPT / REJECT / BLOCKED)

## Keywords
acceptance gate, evidence validation, AI agent governance,
deterministic checks, workspace hygiene, confidence discipline,
transcript truncation, heuristic detection

## Questions for Scout
- Are there existing failure modes for AI agent overclaim?
- Are there reusable patterns for deterministic validation?
- Are there ADRs about evidence-based acceptance?
- What runbooks exist for agent audit workflows?
