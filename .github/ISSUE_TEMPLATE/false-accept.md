---
name: False Accept Report
about: The gate accepted work that should have been rejected or blocked
title: "[FALSE ACCEPT] "
labels: bug, false-accept
assignees: ""
---

## What happened

The gate returned `ACCEPT` or `ACCEPT_WITH_CONDITIONS` for work that should have been `REJECT` or `BLOCKED`.

## Expected decision

<!-- e.g. REJECT, BLOCKED -->

## Actual decision

<!-- e.g. ACCEPT, ACCEPT_WITH_CONDITIONS -->

## Why the work should not have been accepted

<!-- Describe what the agent did wrong that the gate missed -->

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
