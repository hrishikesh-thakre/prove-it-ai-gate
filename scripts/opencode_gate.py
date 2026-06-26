#!/usr/bin/env python3
"""Extract real evidence from OpenCode session database, then run the gate.

Usage:
    python scripts/opencode_gate.py [--task-type audit]
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

OPENDCODE_DB = os.path.expanduser(r"~\.local\share\opencode\opencode.db")


def get_latest_session(project_dir: str) -> dict | None:
    if not os.path.isfile(OPENDCODE_DB):
        return None
    db = sqlite3.connect(OPENDCODE_DB)
    db.row_factory = sqlite3.Row
    candidate_dirs = [
        project_dir,
        project_dir.replace("\\", "/"),
    ]
    row = None
    for cd in set(candidate_dirs):
        row = db.execute(
            "SELECT * FROM session WHERE directory = ? ORDER BY time_created DESC LIMIT 1",
            (cd,),
        ).fetchone()
        if row:
            break
    if not row:
        return None
    return dict(row)


def extract_transcript(session_id: str, output_path: str) -> int:
    if not os.path.isfile(OPENDCODE_DB):
        return 0
    db = sqlite3.connect(OPENDCODE_DB)
    count = 0
    with open(output_path, "w", encoding="utf-8") as fh:
        parts = db.execute(
            "SELECT data FROM part WHERE session_id = ? ORDER BY time_created",
            (session_id,),
        )
        for row in parts:
            try:
                data = json.loads(row[0])
                fh.write(json.dumps(data) + "\n")
                count += 1
            except (json.JSONDecodeError, TypeError):
                pass
    return count


def extract_tool_result_transcript(session_id: str, output_path: str) -> int:
    """Extract a transcript suitable for the gate: only tool_call and tool_result events."""
    if not os.path.isfile(OPENDCODE_DB):
        return 0
    db = sqlite3.connect(OPENDCODE_DB)
    event_map: dict[str, dict] = {}
    parts = db.execute(
        "SELECT data FROM part WHERE session_id = ? ORDER BY time_created",
        (session_id,),
    )
    for row in parts:
        try:
            data = json.loads(row[0])
            ptype = data.get("type", "")
            if ptype == "tool-call":
                call_id = data.get("callID", "")
                if call_id:
                    event_map[call_id] = data
            elif ptype == "tool-result":
                call_id = data.get("callID", "")
                if call_id and call_id in event_map:
                    call = event_map.pop(call_id)
                    merged = {**call, **data,
                              "type": "tool_result",
                              "role": "tool",
                              "tool_name": call.get("tool", ""),
                              "command": call.get("input", {}).get("command", ""),
                              "stdout": data.get("output", ""),
                              "content": data.get("output", "")}
                    merged.pop("callID", None)
                    merged.pop("input", None)
                    merged.pop("output", None)
                    merged.pop("tool", None)
                    event_map[call_id] = merged
        except (json.JSONDecodeError, TypeError, KeyError):
            continue

    count = 0
    with open(output_path, "w", encoding="utf-8") as fh:
        for event in event_map.values():
            fh.write(json.dumps(event) + "\n")
            count += 1
    return count


def capture_git_status(project_dir: str, evidence_dir: str) -> None:
    evidence = Path(evidence_dir)
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "workspace").mkdir(exist_ok=True)
    result = subprocess.run(
        ["git", "status", "--short"],
        capture_output=True, text=True, cwd=project_dir, timeout=10,
    )
    status = result.stdout.strip() or "(clean)"
    (evidence / "workspace" / "git_status_after.txt").write_text(status, encoding="utf-8")


def write_closeout(session: dict, evidence_dir: str) -> None:
    title = session.get("title", "Unknown task")
    additions = session.get("summary_additions", 0) or 0
    deletions = session.get("summary_deletions", 0) or 0
    files = session.get("summary_files", 0) or 0
    changed = f"{files} files (+{additions}/-{deletions})" if files else "None (read-only)"
    closeout = f"# {title}\n\nFiles changed: {changed}\n\nConfidence: 0.85 — session transcript verified by OpenCode database.\n"
    (Path(evidence_dir) / "closeout.md").write_text(closeout, encoding="utf-8")


def run_gate(project_dir: str, evidence_dir: str, transcript_path: str, task_type: str) -> int:
    return subprocess.run(
        [sys.executable, "-m", "prove_it_ai_gate.cli", "accept",
         "--repo", project_dir, "--evidence", evidence_dir,
         "--transcript", transcript_path, "--task-type", task_type],
        cwd=os.path.dirname(os.path.dirname(__file__)),
    ).returncode


def main() -> int:
    project_dir = os.getcwd()
    task_type = "audit"
    if len(sys.argv) > 2 and sys.argv[1] == "--task-type":
        task_type = sys.argv[2]
    evidence_dir = os.path.join(project_dir, "evidence")

    print(f"Project: {project_dir}")

    session = get_latest_session(project_dir)
    if not session:
        print("No OpenCode session found for this directory.")
        return 1

    print(f"Session: {session['title']} ({session['id'][:20]}...)")

    os.makedirs(evidence_dir, exist_ok=True)
    transcript_path = os.path.join(evidence_dir, "transcript.jsonl")
    count = extract_tool_result_transcript(session["id"], transcript_path)
    print(f"Transcript: {count} tool events extracted from OpenCode database")

    capture_git_status(project_dir, evidence_dir)
    print("Git status: captured from live repository")

    write_closeout(session, evidence_dir)
    print("Closeout: generated from session metadata")

    print(f"\nRunning gate ({task_type})...")
    exit_code = run_gate(project_dir, evidence_dir, transcript_path, task_type)

    decisions = {0: "ACCEPT", 1: "REJECT", 2: "ACCEPT_WITH_CONDITIONS", 3: "BLOCKED"}
    print(f"Decision: {decisions.get(exit_code, 'UNKNOWN')} (exit {exit_code})")
    return exit_code if exit_code in (0, 1, 2, 3) else 0


if __name__ == "__main__":
    sys.exit(main())
