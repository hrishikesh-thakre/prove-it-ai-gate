# prove-it-ai-gate

This project builds and uses `prove-it-ai-gate`, a local CLI acceptance gate for AI-generated engineering work.

## Build and Test

```bash
pip install -e .
python -m pytest tests/ -q
python scripts/run_dogfood.py
```

## Project Structure

- `prove_it_ai_gate/` — Core package (CLI, gate engine, checkers)
- `tests/` — 80 tests across 10 test modules
- `policies/` → moved to `prove_it_ai_gate/policies/` (bundled in package)
- `examples/` — Sample evidence, dogfood cases, failure examples
- `docs/` — Documentation
- `scripts/` — gate.sh, gate.ps1, run_dogfood.py

## Conventions

- Python 3.9+, pyyaml only dependency
- `pytest` for testing; no unittest
- All imports use `prove_it_ai_gate.xxx` (package renamed from `src`)
- Keep public repo generic — no real project names, paths, or transcripts
- Evidence checks return `CheckResult` with `Issue` list (types in `prove_it_ai_gate/types.py`)

## Agent Operating Rules

### After completing any task, you MUST:

1. Record your workspace state:
   ```bash
   git status --short > evidence/workspace/git_status_after.txt
   ```

2. Write a closeout summary to `evidence/closeout.md` with:
   - What was done
   - Files changed (or "None" for read-only)
   - Validation results (tests, lint, typecheck)
   - Confidence level with justification
   - Known risks or unresolved issues

3. Save your transcript as `transcript.jsonl`

4. Run the acceptance gate on yourself:
   ```
   ai-gate accept --repo . --evidence ./evidence --transcript transcript.jsonl --task-type audit
   ```
   Or use the `ai-gate` tool.

5. If the gate returns REJECT or BLOCKED, fix the issues before claiming completion.

### Evidence folder contract

Every task must produce:
```
evidence/
  brief.md          — what the task was
  closeout.md       — what was done, what changed, confidence
  risks.md          — known risks and unresolved issues
  commands.jsonl    — commands executed
  changed_files.txt — files modified (one per line)
  workspace/
    git_status_before.txt
    git_status_after.txt
  artifacts/
    inventory.json          — inventory of what was examined
    extracted_findings.json — findings extracted from the work
  validation/
    test_output.txt   — test results (if applicable)
    lint_output.txt   — lint results (if applicable)
    audit_guard.txt   — audit guard result
```

### Do NOT:

- Create helper scripts in the target repo during read-only audits
- Claim "Complete" or "100%" without scoped corpus evidence
- Claim confidence above 0.90 without deterministic evidence artifacts
- Claim "no files changed" if git status shows modifications
- Use truncated tool output as evidence without re-running the tool
