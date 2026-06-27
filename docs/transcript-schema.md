# Transcript Schema

`prove-it-ai-gate` supports JSONL transcripts with a tolerant parser. It does not assume one specific agent platform.

## Minimum Fields

Each transcript event may contain:

```json
{
  "timestamp": "2026-06-26T10:00:00Z",
  "type": "tool_result",
  "role": "tool",
  "tool_name": "shell",
  "command": "git status --short",
  "content": "...",
  "stdout": "...",
  "stderr": "...",
  "exit_code": 0
}
```

## Supported Inputs

- Generic JSONL transcript (one JSON object per line)
- Simple command log JSONL
- Manually assembled evidence transcript
- Daemon-built transcripts from OpenCode SQLite events
- Daemon-built transcripts from Codex hook spool events

## Parser Behavior

The transcript parser:

- Supports missing optional fields gracefully
- Scans `content`, `stdout`, `stderr`, `output`, `result`, and nested message parts for truncation markers
- Avoids false positives from user/assistant discussion of words like `truncated`
- Preserves line numbers for evidence
- Fails safely on malformed JSONL (flags as warning, continues parsing)

## Codex Hook Events

Codex supervision is hook-first. Hook events are written by
`prove_it_ai_gate.codex_hooks` to the daemon spool and then aggregated into the
run transcript by the daemon.

Canonical fields may include:

```json
{
  "source": "codex_hook",
  "agent": "codex",
  "event_id": "codex:...",
  "session_id": "ses_...",
  "cwd": "/path/to/project",
  "hook_event_name": "PreToolUse",
  "permission_mode": "default",
  "turn_id": "turn_...",
  "tool_name": "bash",
  "tool_use_id": "tool_...",
  "tool_input": {"command": "git status --short"},
  "tool_response": {"exit_code": 0},
  "decision": "allow",
  "violations": []
}
```

`SessionStart` is treated as the true before-capture signal. If the first
observed event is not `SessionStart`, the run must set `late_snapshot=true`.

Codex session JSONL transcript parsing is fallback/reconciliation only. A
fallback-derived run must set `fallback_only=true` and must not be reported as
hook-first coverage.

## OpenCode Events

OpenCode supervision reads the Desktop SQLite database and writes transcript
events with `source=opencode_sqlite`. Events are deduped by session id plus
SQLite `part.rowid`.

## Truncation Markers

The transcript truncation check looks for these markers in tool output:

- `truncated`
- `output omitted`
- `showing first`
- `too many results`
- `partial results`
- `max output`
- `results omitted`
