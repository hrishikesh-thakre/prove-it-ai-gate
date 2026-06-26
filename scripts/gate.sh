#!/usr/bin/env bash
set -euo pipefail

CONFIG="${AI_GATE_CONFIG:-.ai-gate/config.yaml}"
BRIEF="${1:-}"
EVIDENCE="${EVIDENCE:-./evidence}"
WIKI_PATH="${WIKI_PATH:-}"
LEARNINGS="${LEARNINGS:-./learnings}"
TRANSCRIPT="${TRANSCRIPT:-./transcript.jsonl}"
TASK_TYPE="${TASK_TYPE:-audit}"

if [ -z "$BRIEF" ]; then
  echo "Usage: ./scripts/gate.sh brief.md"
  exit 1
fi

if [ ! -f "$BRIEF" ]; then
  echo "Brief not found: $BRIEF"
  exit 1
fi

if [ ! -d "$EVIDENCE" ]; then
  echo "Evidence folder not found: $EVIDENCE"
  echo "Create an evidence/ folder with at minimum: brief.md, closeout.md,"
  echo "  workspace/git_status_before.txt, workspace/git_status_after.txt"
  exit 1
fi

if [ ! -f "$TRANSCRIPT" ]; then
  echo "Transcript not found: $TRANSCRIPT"
  echo "Save your agent transcript as transcript.jsonl before running the gate."
  exit 1
fi

if [ -n "$WIKI_PATH" ] && [ -d "$WIKI_PATH" ]; then
  echo "=== Scout: reuse scan ==="
  ai-gate reuse-scan \
    --brief "$BRIEF" \
    --local-wiki "$WIKI_PATH" \
    --output "$LEARNINGS" || echo "Scout failed (non-blocking)"
else
  echo "=== Scout: skipped (no WIKI_PATH or wiki not found) ==="
fi

echo "=== Gate: acceptance check ==="
ai-gate accept \
  --repo . \
  --evidence "$EVIDENCE" \
  --transcript "$TRANSCRIPT" \
  --task-type "$TASK_TYPE"

LATEST_REPORT="$(ls -t acceptance_report_*.json 2>/dev/null | head -n 1 || true)"

if [ -n "$LATEST_REPORT" ]; then
  mkdir -p "$LEARNINGS"
  echo "=== Capture: reusable learnings ==="
  ai-gate capture \
    --report-json "$LATEST_REPORT" \
    --evidence "$EVIDENCE" \
    --output "$LEARNINGS"
else
  echo "=== Capture: skipped (no acceptance report found) ==="
fi

echo "=== Done ==="
test -n "$LATEST_REPORT" && echo "Report: $LATEST_REPORT"
echo "Learnings: $LEARNINGS"
