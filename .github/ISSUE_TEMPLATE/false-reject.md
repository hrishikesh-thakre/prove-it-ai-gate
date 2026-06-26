---
name: False Reject Report
about: The gate rejected or blocked work that should have been accepted
title: "[FALSE REJECT] "
labels: bug, false-reject
assignees: ""
---

## What happened

The gate returned `REJECT` or `BLOCKED` for work that should have been `ACCEPT`.

## Expected decision

<!-- e.g. ACCEPT -->

## Actual decision

<!-- e.g. REJECT, BLOCKED -->

## Why the work should have been accepted

<!-- Describe why the evidence was sufficient and the rejection was incorrect -->

## Which check(s) triggered incorrectly

<!-- e.g. confidence_claim_check, heuristic_extraction_check -->

## Transcript

<!-- Attach or paste a minimal transcript.jsonl that reproduces the issue -->

## Evidence folder

<!-- Attach a minimal evidence folder structure. Use fake/generic data only. -->

## Policy used

<!-- e.g. read_only_audit, code_change, corpus_audit -->

## Environment

- OS: <!-- e.g. macOS 14, Ubuntu 22.04, Windows 11 -->
- Python version: <!-- e.g. 3.11 -->
- prove-it-ai-gate version: <!-- e.g. 0.3.1 -->
