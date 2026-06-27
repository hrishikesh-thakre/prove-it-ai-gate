# Automated Evidence Supervisor

The automated evidence supervisor is the experimental path for collecting
before/after evidence without requiring users to run manual commands around
every agent session.

## Goal

The supervisor should make evidence collection automatic while keeping outcomes
truthful:

- no fabricated validation passes
- no pretend pre-session baseline when capture started late
- no claim of full Codex coverage from transcript fallback
- final decisions remain `ACCEPT`, `ACCEPT_WITH_CONDITIONS`, `REJECT`, or
  `BLOCKED`

## Setup

For a project:

```bash
ai-gate setup daemon --project-dir . --task-type audit
ai-gate daemon start
ai-gate daemon status
```

`setup daemon` registers the project in the daemon state directory. It does not
write daemon state into the project repository. Evidence still goes under the
project evidence root.

Direct registry commands remain available:

```bash
ai-gate daemon register --project-dir . --task-type audit
ai-gate daemon unregister --project-dir .
ai-gate daemon list
```

## State And Evidence Locations

Daemon-owned state lives in the user config directory:

- `daemon_state.json`
- `daemon.lock`
- `daemon.stop`
- `spool/codex_events.jsonl`
- `logs/daemon.jsonl`
- `logs/daemon.log`

Project evidence is run-scoped:

```text
evidence/
  latest.json
  reports/
    index.json
    index.html
  runs/
    <timestamp>-<agent>-<session-id>/
      metadata.json
      transcript.jsonl
      commands.jsonl
      acceptance_report_*.json
      acceptance_report_*.md
      acceptance_report_*.csv
```

## Status Model

`ai-gate daemon status` separates project state from coverage.

Project state:

- `UNSUPERVISED`
- `FALLBACK_ONLY`
- `RUNNING`
- `BLOCKED`
- `REJECT`
- `ACCEPT_WITH_CONDITIONS`
- `ACCEPT`

Coverage:

- `UNSUPERVISED`
- `HOOKS_CONFIGURED_UNTRUSTED_UNVERIFIED`
- `HOOKS_OBSERVED`
- `FALLBACK_ONLY`

Use JSON output for automation:

```bash
ai-gate daemon status --json
```

Every non-accepted state includes a `required_next_action`/`next_action`.

## Logs

Use:

```bash
ai-gate daemon logs --tail 50
ai-gate daemon logs --tail 50 --json
```

Logs are operational metadata only. They record lifecycle events, registration,
startup checks, adapter health, recovery actions, run start/finalization,
validation result summaries, and acceptance decisions.

Daemon logs redact raw stdout, stderr, transcript bodies, and tool outputs.
Evidence artifacts still preserve captured tool output because the acceptance
gate needs the real evidence record.

## Crash Recovery

The daemon uses:

- an owner lock file to avoid concurrent daemon instances
- PID liveness and heartbeat checks for stale lock recovery
- restart recovery for partially captured runs
- line-level Codex spool checkpoints

If a run has terminal metadata and existing report paths, recovery reuses the
existing report instead of writing duplicate acceptance reports.

## OpenCode Adapter

OpenCode supervision uses the OpenCode Desktop SQLite database.

Behavior:

- watches registered projects only
- checks the SQLite schema before polling
- dedupes events by session id and `part.rowid`
- starts a run when a matching session appears
- marks `late_snapshot=true` if the session already had rows before capture
- finalizes on idle timeout, daemon stop, or daemon restart recovery

Dogfood command:

```bash
ai-gate daemon start --foreground --idle-seconds 30
```

Then run a real OpenCode Desktop session in the registered project and inspect:

```bash
ai-gate daemon status
ai-gate reports show latest --project-dir .
```

## Codex Adapter

Codex supervision is hook-first.

Install hooks:

```bash
ai-gate setup codex --project-dir . --task-type audit
```

This writes project-local `.codex/hooks.json` for:

- `SessionStart`
- `PreToolUse`
- `PostToolUse`
- `Stop`

Codex must review and trust non-managed hooks before they run. After setup,
restart Codex or start a new session, open `/hooks`, review the hook, and trust
it once.

Coverage states:

- hooks configured but no real hook event observed:
  `HOOKS_CONFIGURED_UNTRUSTED_UNVERIFIED`
- hook event observed:
  `HOOKS_OBSERVED`
- transcript reconstruction only:
  `FALLBACK_ONLY`

Transcript polling is fallback/reconciliation only. It must not be used to claim
hook-first coverage.

## Windows Startup

Windows login startup is supported experimentally:

```bash
ai-gate daemon startup-status
ai-gate daemon install-startup --dry-run
ai-gate daemon install-startup
ai-gate daemon install-startup --force
ai-gate daemon uninstall-startup
```

The generated startup command launches `ai-gate daemon start` and appends
startup failures to the daemon text log. Installation is idempotent. A stale
startup command is reported and requires `--force` to replace.

## Report Browsing

Report browsing is static and does not start a local web server:

```bash
ai-gate reports list --project-dir .
ai-gate reports show latest --project-dir .
ai-gate reports show latest --project-dir . --json
ai-gate reports show latest --project-dir . --markdown
ai-gate reports open latest --project-dir .
```

The report browser reads `metadata.json` and acceptance report JSON/Markdown
only. It does not parse transcripts for browsing.

Finalized runs regenerate:

```text
evidence/reports/index.json
evidence/reports/index.html
```

## Validation

For code-change task types, the daemon only auto-detects conservative
validation commands:

- Python tests
- Node `test`
- Node `lint`
- Node `typecheck`

It does not infer deploy, publish, migrate, seed, clean, install, or destructive
commands. Missing validation blocks code-change acceptance. Timed-out
validation is `BLOCKED`. Failed validation is `REJECT`.

