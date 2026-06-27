# Evidence Folder Contract

Every agent task must produce an evidence folder. The folder structure and required files depend on task type.

## Directory Structure

```
evidence/
  latest.json
  reports/
    index.json
    index.html
  runs/<timestamp>-<agent>-<session-id>/
    metadata.json
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

The automated supervisor daemon writes run-scoped evidence under
`evidence/runs/<timestamp>-<agent>-<session-id>/` and updates
`evidence/latest.json` to point at the newest run for the project. The files
listed above are required inside each run folder. Finalized runs also refresh a
static report index under `evidence/reports/`.

Daemon-owned state is not stored in the project repo. The state directory holds
registry data, locks, Codex hook spool files, and daemon logs.

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

## Supervisor Metadata

Daemon-generated runs include `metadata.json` with:

- `run_id`
- `agent`
- `session_id`
- `project_dir`
- `evidence_root`
- `task_type`
- `status`
- `late_snapshot`
- `decision`
- `required_next_action`
- acceptance report paths (`acceptance_report_json`,
  `acceptance_report_md`, `acceptance_report_csv`)
- validation summary

If a daemon detects a session after activity has already started, it must set
`late_snapshot=true` in `metadata.json` and document the limitation in
`risks.md`. The captured baseline must not be presented as a true pre-session
snapshot.

Codex transcript reconstruction is fallback evidence. Fallback-generated runs
must set `fallback_only=true` and must not be reported as hook-first coverage.

## Report Index

The static report browser uses:

- `metadata.json`
- `acceptance_report_*.json`
- `acceptance_report_*.md`

It must not parse transcripts for browsing. Report paths recorded in metadata
must stay inside the run folder.

## Status Contract

Daemon status separates project state from supervision coverage.

Project states:

- `UNSUPERVISED`
- `FALLBACK_ONLY`
- `RUNNING`
- `BLOCKED`
- `REJECT`
- `ACCEPT_WITH_CONDITIONS`
- `ACCEPT`

Coverage states:

- `UNSUPERVISED`
- `HOOKS_CONFIGURED_UNTRUSTED_UNVERIFIED`
- `HOOKS_OBSERVED`
- `FALLBACK_ONLY`

Every non-accepted state should include a concrete `required_next_action`.

## Operational Logs

Daemon logs are stored in the daemon state directory under `logs/daemon.jsonl`
and `logs/daemon.log`.

Logs may include lifecycle events, adapter health, recovery actions, validation
summaries, and acceptance decisions. They must not include raw transcript
bodies or raw tool stdout/stderr.

## Validation Sentinels

For code-change task types, missing or timed-out validation is blocking
evidence:

- `AI_GATE_VALIDATION_MISSING` means no conservative command was auto-detected.
- `AI_GATE_VALIDATION_TIMEOUT` means validation hung and the run is `BLOCKED`.
- `AI_GATE_VALIDATION_FAILED` means a detected validation command failed and the
  run is `REJECT`.

Sentinel files are intentionally non-empty evidence of the blocker or failure;
they must not be treated as passing validation.
