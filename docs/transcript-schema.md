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

## Parser Behavior

The transcript parser:

- Supports missing optional fields gracefully
- Scans `content`, `stdout`, `stderr`, `output`, `result`, and nested message parts for truncation markers
- Avoids false positives from user/assistant discussion of words like `truncated`
- Preserves line numbers for evidence
- Fails safely on malformed JSONL (flags as warning, continues parsing)

## Truncation Markers

The transcript truncation check looks for these markers in tool output:

- `truncated`
- `output omitted`
- `showing first`
- `too many results`
- `partial results`
- `max output`
- `results omitted`
