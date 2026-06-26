# Workspace Hygiene

The workspace hygiene check verifies that the agent did not unexpectedly modify the target repository.

## What It Checks

1. **Git status before and after**: Compares `git_status_before.txt` and `git_status_after.txt` from the evidence folder
2. **Read-only task violations**: If the task is a read-only audit, any repository modifications are rejected
3. **Closeout honesty**: If `closeout.md` claims "no files changed" but git status shows modifications, the task is rejected
4. **Scratch file detection**: Flags temporary/scratch files created in the target repo (e.g., `scratch_*.py`, `tmp_*.sh`)
5. **Live git fallback**: If evidence files are missing, falls back to live `git status` on the target repo with a warning

## Required Evidence

- `workspace/git_status_before.txt`
- `workspace/git_status_after.txt`

## Allowed Scratch Directory

Use `--scratch-dir` to designate an allowed location for temporary files:

```bash
ai-gate accept --repo ./target --scratch-dir ./scratch ...
```

Files created in the scratch directory are not flagged, even if they match scratch patterns (`.py`, `.ps1`, `.sh`, etc.).

## Example: Rejected

```
Decision: REJECT

workspace_hygiene_check: FAILED
  Read-only task modified target repo
  Closeout claims no changes but git status shows modifications
  Scratch file: tmp_analysis.py
```
