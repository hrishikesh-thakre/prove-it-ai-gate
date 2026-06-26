# Evidence Folder Contract

Every agent task must produce an evidence folder. The folder structure and required files depend on task type.

## Directory Structure

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

## Required by Task Type

### Read-Only Audit

- `transcript.jsonl`
- `brief.md` (scoped corpus definition)
- `artifacts/inventory.json` (complete inventory or partial-sample warning)
- `artifacts/extracted_findings.json` (deterministic extraction artifact)
- `workspace/git_status_before.txt` and `git_status_after.txt`
- `validation/audit_guard.txt`
- `risks.md` (confidence limits)

### Code Change

- `changed_files.txt`
- `workspace/diff_summary.patch`
- `validation/test_output.txt`
- `validation/lint_output.txt`
- `validation/typecheck_output.txt`
- `risks.md` (risk classification, unresolved risks)

### Corpus Audit

- `artifacts/inventory.json` (full inventory)
- `commands.jsonl` (extraction script/command)
- `artifacts/extracted_findings.json` (extracted data artifact)
- `brief.md` (scope statement)
- `risks.md` (heuristic limitations)
- `validation/audit_guard.txt`

## File Quality Rules

Evidence files must:

- Be non-empty (not zero bytes)
- Contain usable content (not just placeholders like "TODO" or "TBD")
- Not contain truncated output markers themselves
- Match expected schema where applicable (valid JSON for `.json`, valid YAML front matter for `.md`)
