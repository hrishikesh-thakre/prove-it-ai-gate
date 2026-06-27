from __future__ import annotations

import io
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from prove_it_ai_gate.cli import DEFAULT_POLICY_DIR
from prove_it_ai_gate.codex_hooks import run_codex_hook
from prove_it_ai_gate.daemon import (
    SupervisorDaemon,
    check_opencode_compatibility,
    daemon_owner_status,
    install_windows_startup,
    startup_status,
    status_rows,
    uninstall_windows_startup,
)
from prove_it_ai_gate.daemon_log import read_daemon_logs, write_daemon_log
from prove_it_ai_gate.daemon_state import list_projects, register_project, unregister_project
from prove_it_ai_gate.evidence_builder import EvidenceRunBuilder
from prove_it_ai_gate.evidence_checker import check_evidence_folder
from prove_it_ai_gate.report_browser import build_report_index, collect_reports, open_report_ref
from prove_it_ai_gate.validation_runner import (
    ValidationCommand,
    _run_command,
    detect_validation_commands,
    run_validation,
)


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, capture_output=True, text=True, timeout=20)


def _make_opencode_db(path: Path, project_dir: Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row
    db.execute("""
        CREATE TABLE session (
            id TEXT PRIMARY KEY,
            directory TEXT,
            title TEXT,
            time_created TEXT
        )
    """)
    db.execute("""
        CREATE TABLE part (
            rowid INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            data TEXT,
            time_created TEXT
        )
    """)
    db.execute(
        "INSERT INTO session(id, directory, title, time_created) VALUES (?, ?, ?, ?)",
        ("ses_test", str(project_dir.resolve()).replace("\\", "/"), "Test", "2026-01-01T00:00:00"),
    )
    db.commit()
    return db


def _insert_tool(db: sqlite3.Connection, session_id: str, command: str, ts: str = "2026-01-01T00:00:01") -> None:
    data = {
        "type": "tool",
        "tool": "bash",
        "state": {
            "input": {"command": command},
            "output": f"output for {command}",
            "status": "completed",
        },
    }
    db.execute(
        "INSERT INTO part(session_id, data, time_created) VALUES (?, ?, ?)",
        (session_id, json.dumps(data), ts),
    )
    db.commit()


def test_registry_add_remove_list(tmp_path):
    state_path = tmp_path / "state.json"
    project = tmp_path / "project"
    project.mkdir()

    first = register_project(str(project), task_type="audit", state_path=state_path)
    second = register_project(str(project), task_type="code_change", state_path=state_path)

    projects = list_projects(state_path)
    assert len(projects) == 1
    assert first["project_dir"] == second["project_dir"]
    assert projects[0]["task_type"] == "code_change"
    assert unregister_project(str(project), state_path=state_path)
    assert list_projects(state_path) == []


def test_daemon_status_without_projects_reports_opencode_health(tmp_path):
    env = os.environ.copy()
    env["AI_GATE_DAEMON_HOME"] = str(tmp_path / "daemon-home")
    result = subprocess.run(
        [sys.executable, "-m", "prove_it_ai_gate.cli", "daemon", "status"],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0
    assert "No registered projects." in result.stdout
    assert "OpenCode DB:" in result.stdout


def test_validation_missing_sentinel_blocks_code_change(tmp_path):
    evidence = tmp_path / "evidence"
    validation = evidence / "validation"
    run_validation(str(tmp_path), str(validation), task_type="code_change", timeout_seconds=1)

    result = check_evidence_folder(str(evidence), "code_change")
    assert result.status == "blocked"
    assert any("AI_GATE_VALIDATION_MISSING" in issue.message for issue in result.issues)


def test_validation_timeout_is_blocker(tmp_path):
    cmd = ValidationCommand(
        kind="test",
        command=[sys.executable, "-c", "import time; time.sleep(2)"],
        source="test",
    )
    result = _run_command(str(tmp_path), cmd, timeout_seconds=1)
    assert result.status == "timeout"
    assert "AI_GATE_VALIDATION_TIMEOUT" in result.output


def test_validation_detection_skips_destructive_scripts(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({
        "scripts": {
            "test": "npm install && jest",
            "lint": "eslint .",
            "typecheck": "tsc --noEmit",
            "deploy": "vercel deploy",
        }
    }), encoding="utf-8")
    commands = detect_validation_commands(str(tmp_path))
    assert "test" not in commands
    assert commands["lint"].command == ["npm", "run", "lint"]
    assert commands["typecheck"].command == ["npm", "run", "typecheck"]


def test_evidence_builder_audit_produces_terminal_report(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    _init_git_repo(project)
    (project / "README.md").write_text("hello\n", encoding="utf-8")

    builder = EvidenceRunBuilder.create(
        project_dir=str(project),
        evidence_root=str(project / "evidence"),
        task_type="audit",
        agent="test",
        session_id="ses",
        late_snapshot=False,
    )
    metadata = builder.finalize(DEFAULT_POLICY_DIR, validation_timeout=1)

    assert metadata["decision"] in {"ACCEPT", "ACCEPT_WITH_CONDITIONS", "REJECT"}
    assert metadata["decision"] != "BLOCKED"
    assert (builder.run_path / "brief.md").is_file()
    assert (builder.run_path / "artifacts" / "inventory.json").is_file()
    assert (builder.run_path / "workspace" / "git_status_before.txt").is_file()
    assert (builder.run_path / "workspace" / "git_status_after.txt").is_file()


def test_opencode_adapter_dedupes_by_rowid_with_same_timestamp(tmp_path):
    state_path = tmp_path / "state.json"
    project = tmp_path / "project"
    project.mkdir()
    _init_git_repo(project)
    db_path = tmp_path / "opencode.db"
    db = _make_opencode_db(db_path, project)
    _insert_tool(db, "ses_test", "git status", ts="2026-01-01T00:00:01")
    _insert_tool(db, "ses_test", "python -m pytest tests/ -q", ts="2026-01-01T00:00:01")
    db.close()

    register_project(str(project), task_type="audit", state_path=state_path)
    daemon = SupervisorDaemon(
        policy_dir=DEFAULT_POLICY_DIR,
        state_path=state_path,
        opencode_db=str(db_path),
        idle_seconds=999,
        validation_timeout=1,
    )
    daemon.process_opencode()
    daemon.process_opencode()

    rows = status_rows(state_path)
    run_path = Path(rows[0]["latest_run_path"])
    lines = [line for line in (run_path / "transcript.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 2


def test_opencode_compatibility_reports_fixture_schema(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    db_path = tmp_path / "opencode.db"
    db = _make_opencode_db(db_path, project)
    _insert_tool(db, "ses_test", "git status")
    db.close()

    health = check_opencode_compatibility(str(db_path))

    assert health["status"] == "compatible"
    assert health["compatible"] is True
    assert health["sample_tool_shape"] == "compatible"


def test_opencode_compatibility_reports_schema_drift(tmp_path):
    db_path = tmp_path / "bad-opencode.db"
    db = sqlite3.connect(str(db_path))
    db.execute("CREATE TABLE session (id TEXT PRIMARY KEY)")
    db.execute("CREATE TABLE part (session_id TEXT)")
    db.commit()
    db.close()

    health = check_opencode_compatibility(str(db_path))

    assert health["status"] == "incompatible"
    assert health["compatible"] is False
    assert "missing required columns" in health["message"]


def test_opencode_late_snapshot_metadata(tmp_path):
    state_path = tmp_path / "state.json"
    project = tmp_path / "project"
    project.mkdir()
    _init_git_repo(project)
    db_path = tmp_path / "opencode.db"
    db = _make_opencode_db(db_path, project)
    _insert_tool(db, "ses_test", "git status")
    db.close()

    register_project(str(project), task_type="audit", state_path=state_path)
    daemon = SupervisorDaemon(DEFAULT_POLICY_DIR, state_path=state_path, opencode_db=str(db_path), idle_seconds=999)
    daemon.process_opencode()

    run_path = Path(status_rows(state_path)[0]["latest_run_path"])
    metadata = json.loads((run_path / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["late_snapshot"] is True
    assert "late_snapshot=true" in (run_path / "risks.md").read_text(encoding="utf-8")
    row = status_rows(state_path, opencode_db=str(db_path))[0]
    assert row["late_snapshot"] is True
    assert row["opencode_db_status"] == "compatible"


def test_opencode_fixture_produces_final_acceptance_report(tmp_path):
    state_path = tmp_path / "state.json"
    project = tmp_path / "project"
    project.mkdir()
    _init_git_repo(project)
    db_path = tmp_path / "opencode.db"
    db = _make_opencode_db(db_path, project)
    _insert_tool(db, "ses_test", "git status --short")
    db.close()

    register_project(str(project), task_type="audit", state_path=state_path)
    daemon = SupervisorDaemon(
        DEFAULT_POLICY_DIR,
        state_path=state_path,
        opencode_db=str(db_path),
        idle_seconds=0,
        validation_timeout=1,
    )
    daemon.process_opencode()

    row = status_rows(state_path)[0]
    run_path = Path(row["latest_run_path"])
    assert row["decision"] in {"ACCEPT", "ACCEPT_WITH_CONDITIONS", "REJECT"}
    assert row["decision"] != "BLOCKED"
    assert list(run_path.glob("acceptance_report_*.json"))
    assert list(run_path.glob("acceptance_report_*.md"))
    assert list(run_path.glob("acceptance_report_*.csv"))
    assert row["finalize_reason"] == "opencode-idle-timeout"
    assert row["opencode_db_status"] == "compatible"


def test_opencode_idle_finalization_is_not_repeated(tmp_path):
    state_path = tmp_path / "state.json"
    project = tmp_path / "project"
    project.mkdir()
    _init_git_repo(project)
    db_path = tmp_path / "opencode.db"
    db = _make_opencode_db(db_path, project)
    _insert_tool(db, "ses_test", "git status --short")
    db.close()

    register_project(str(project), task_type="audit", state_path=state_path)
    daemon = SupervisorDaemon(
        DEFAULT_POLICY_DIR,
        state_path=state_path,
        opencode_db=str(db_path),
        idle_seconds=0,
        validation_timeout=1,
    )
    daemon.process_opencode()
    first_run = status_rows(state_path, opencode_db=str(db_path))[0]["latest_run_path"]
    daemon.process_opencode()
    second_run = status_rows(state_path, opencode_db=str(db_path))[0]["latest_run_path"]

    assert second_run == first_run
    assert len(list(Path(first_run).glob("acceptance_report_*.json"))) == 1


def test_restart_recovery_finalizes_partial_run(tmp_path):
    state_path = tmp_path / "state.json"
    project = tmp_path / "project"
    project.mkdir()
    _init_git_repo(project)
    db_path = tmp_path / "opencode.db"
    db = _make_opencode_db(db_path, project)
    _insert_tool(db, "ses_test", "git status --short")
    db.close()

    register_project(str(project), task_type="audit", state_path=state_path)
    daemon = SupervisorDaemon(
        DEFAULT_POLICY_DIR,
        state_path=state_path,
        opencode_db=str(db_path),
        idle_seconds=999,
        validation_timeout=1,
    )
    daemon.process_opencode()
    running_row = status_rows(state_path, opencode_db=str(db_path))[0]
    assert running_row["status"] == "RUNNING"

    restarted = SupervisorDaemon(
        DEFAULT_POLICY_DIR,
        state_path=state_path,
        opencode_db=str(db_path),
        idle_seconds=999,
        validation_timeout=1,
    )
    finalized = restarted.recover_running_projects()

    row = status_rows(state_path, opencode_db=str(db_path))[0]
    assert finalized
    assert row["status"] in {"ACCEPT", "ACCEPT_WITH_CONDITIONS", "REJECT"}
    assert row["finalize_reason"] == "daemon-restart-recovery"
    assert Path(row["latest_run_path"], "metadata.json").is_file()


def test_codex_hook_spool_and_daemon_finalization(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AI_GATE_DAEMON_HOME", str(home))
    project = tmp_path / "project"
    project.mkdir()
    _init_git_repo(project)
    (project / "README.md").write_text("hello\n", encoding="utf-8")
    register_project(str(project), task_type="audit")

    events = [
        {"hook_event_name": "SessionStart", "session_id": "codex_ses", "cwd": str(project)},
        {
            "hook_event_name": "PreToolUse",
            "session_id": "codex_ses",
            "tool_use_id": "tool_1",
            "cwd": str(project),
            "tool_name": "Bash",
            "tool_input": {"command": "git status --short"},
        },
        {
            "hook_event_name": "PostToolUse",
            "session_id": "codex_ses",
            "tool_use_id": "tool_1",
            "cwd": str(project),
            "tool_name": "Bash",
            "tool_input": {"command": "git status --short"},
            "tool_output": "clean",
        },
        {"hook_event_name": "Stop", "session_id": "codex_ses", "cwd": str(project)},
    ]

    old_stdin = sys.stdin
    old_stdout = sys.stdout
    try:
        for event in events:
            sys.stdin = io.StringIO(json.dumps(event))
            sys.stdout = io.StringIO()
            assert run_codex_hook() == 0
    finally:
        sys.stdin = old_stdin
        sys.stdout = old_stdout

    from prove_it_ai_gate import codex_transcript

    monkeypatch.setattr(codex_transcript, "extract_transcript", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("transcript polling used")))
    daemon = SupervisorDaemon(DEFAULT_POLICY_DIR, validation_timeout=1)
    daemon.process_codex_spool()

    row = status_rows()[0]
    assert row["decision"] in {"ACCEPT", "ACCEPT_WITH_CONDITIONS", "REJECT"}
    assert row["decision"] != "BLOCKED"
    assert row["codex_coverage"] == "HOOKS_OBSERVED"
    assert Path(row["latest_run_path"], "transcript.jsonl").is_file()
    assert list(Path(row["latest_run_path"]).glob("acceptance_report_*.json"))
    assert "codex_transcript" not in Path(row["latest_run_path"], "transcript.jsonl").read_text(encoding="utf-8")


def test_codex_missing_session_start_sets_late_snapshot(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AI_GATE_DAEMON_HOME", str(home))
    project = tmp_path / "project"
    project.mkdir()
    _init_git_repo(project)
    register_project(str(project), task_type="audit")

    event = {
        "hook_event_name": "PreToolUse",
        "session_id": "codex_late",
        "tool_use_id": "tool_1",
        "cwd": str(project),
        "tool_name": "Bash",
        "tool_input": {"command": "git status --short"},
    }
    old_stdin = sys.stdin
    old_stdout = sys.stdout
    try:
        sys.stdin = io.StringIO(json.dumps(event))
        sys.stdout = io.StringIO()
        assert run_codex_hook() == 0
    finally:
        sys.stdin = old_stdin
        sys.stdout = old_stdout

    daemon = SupervisorDaemon(DEFAULT_POLICY_DIR, validation_timeout=1)
    daemon.process_codex_spool()

    row = status_rows()[0]
    metadata = json.loads(Path(row["latest_run_path"], "metadata.json").read_text(encoding="utf-8"))
    assert row["status"] == "RUNNING"
    assert row["late_snapshot"] is True
    assert metadata["late_snapshot"] is True


def test_codex_spool_dedupes_duplicate_event_ids(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AI_GATE_DAEMON_HOME", str(home))
    project = tmp_path / "project"
    project.mkdir()
    _init_git_repo(project)
    register_project(str(project), task_type="audit")

    spool = home / "spool" / "codex_events.jsonl"
    spool.parent.mkdir(parents=True)
    events = [
        {"hook_event_name": "SessionStart", "session_id": "codex_dupe", "cwd": str(project), "event_id": "evt-start"},
        {
            "hook_event_name": "PreToolUse",
            "session_id": "codex_dupe",
            "tool_use_id": "tool_1",
            "cwd": str(project),
            "tool_name": "Bash",
            "tool_input": {"command": "git status --short"},
            "event_id": "evt-tool",
        },
        {
            "hook_event_name": "PreToolUse",
            "session_id": "codex_dupe",
            "tool_use_id": "tool_1",
            "cwd": str(project),
            "tool_name": "Bash",
            "tool_input": {"command": "git status --short"},
            "event_id": "evt-tool",
        },
        {"hook_event_name": "Stop", "session_id": "codex_dupe", "cwd": str(project), "event_id": "evt-stop"},
    ]
    spool.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")

    daemon = SupervisorDaemon(DEFAULT_POLICY_DIR, validation_timeout=1)
    daemon.process_codex_spool()

    row = status_rows()[0]
    transcript = Path(row["latest_run_path"], "transcript.jsonl").read_text(encoding="utf-8").splitlines()
    assert len([line for line in transcript if line.strip()]) == 3


def test_codex_spool_dedupes_replayed_hook_without_hook_event_id(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AI_GATE_DAEMON_HOME", str(home))
    project = tmp_path / "project"
    project.mkdir()
    _init_git_repo(project)
    register_project(str(project), task_type="audit")

    events = [
        {"hook_event_name": "SessionStart", "session_id": "codex_replay", "cwd": str(project)},
        {
            "hook_event_name": "PreToolUse",
            "session_id": "codex_replay",
            "turn_id": "turn_1",
            "tool_use_id": "tool_1",
            "cwd": str(project),
            "tool_name": "Bash",
            "tool_input": {"command": "git status --short"},
        },
        {
            "hook_event_name": "PreToolUse",
            "session_id": "codex_replay",
            "turn_id": "turn_1",
            "tool_use_id": "tool_1",
            "cwd": str(project),
            "tool_name": "Bash",
            "tool_input": {"command": "git status --short"},
        },
        {"hook_event_name": "Stop", "session_id": "codex_replay", "turn_id": "turn_1", "cwd": str(project)},
    ]

    old_stdin = sys.stdin
    old_stdout = sys.stdout
    try:
        for event in events:
            sys.stdin = io.StringIO(json.dumps(event))
            sys.stdout = io.StringIO()
            assert run_codex_hook() == 0
    finally:
        sys.stdin = old_stdin
        sys.stdout = old_stdout

    daemon = SupervisorDaemon(DEFAULT_POLICY_DIR, validation_timeout=1)
    daemon.process_codex_spool()

    row = status_rows()[0]
    transcript = Path(row["latest_run_path"], "transcript.jsonl").read_text(encoding="utf-8").splitlines()
    assert len([line for line in transcript if line.strip()]) == 3


def test_codex_hook_input_parse_failure_finalizes_as_blocked(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AI_GATE_DAEMON_HOME", str(home))
    project = tmp_path / "project"
    project.mkdir()
    _init_git_repo(project)
    register_project(str(project), task_type="audit")
    monkeypatch.chdir(project)

    old_stdin = sys.stdin
    old_stdout = sys.stdout
    try:
        sys.stdin = io.StringIO("not valid json {{{")
        sys.stdout = io.StringIO()
        assert run_codex_hook() == 0
        assert sys.stdout.getvalue().strip() == ""
    finally:
        sys.stdin = old_stdin
        sys.stdout = old_stdout

    daemon = SupervisorDaemon(DEFAULT_POLICY_DIR, validation_timeout=1)
    daemon.process_codex_spool()
    daemon.finalize_all(reason="test-finalize")

    row = status_rows()[0]
    assert row["decision"] == "BLOCKED"
    transcript = Path(row["latest_run_path"], "transcript.jsonl").read_text(encoding="utf-8")
    assert "codex-hook-fail-open" in transcript


def test_codex_pretool_destructive_command_spooled_as_block(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AI_GATE_DAEMON_HOME", str(home))
    project = tmp_path / "project"
    project.mkdir()
    register_project(str(project), task_type="audit")

    event = {
        "hook_event_name": "PreToolUse",
        "session_id": "codex_ses",
        "tool_use_id": "tool_bad",
        "cwd": str(project),
        "tool_name": "Bash",
        "tool_input": {"command": "rm -rf / --no-preserve-root"},
    }
    old_stdin = sys.stdin
    old_stdout = sys.stdout
    try:
        sys.stdin = io.StringIO(json.dumps(event))
        sys.stdout = io.StringIO()
        run_codex_hook()
        response = json.loads(sys.stdout.getvalue())
    finally:
        sys.stdin = old_stdin
        sys.stdout = old_stdout

    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert response["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    spool = home / "spool" / "codex_events.jsonl"
    spooled = json.loads(spool.read_text(encoding="utf-8").splitlines()[0])
    assert spooled["hook_event_name"] == "PreToolUse"
    assert spooled["tool_use_id"] == "tool_bad"
    assert spooled["violations"][0]["severity"] == "blocker"


def test_setup_codex_writes_hooks_and_preserves_opencode_config(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AI_GATE_DAEMON_HOME", str(home))
    project = tmp_path / "project"
    project.mkdir()
    (project / ".opencode").mkdir()
    (project / ".opencode" / "keep.txt").write_text("keep\n", encoding="utf-8")
    (project / "opencode.json").write_text("{}\n", encoding="utf-8")

    env = os.environ.copy()
    env["AI_GATE_DAEMON_HOME"] = str(home)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "prove_it_ai_gate.cli",
            "setup",
            "codex",
            "--project-dir",
            str(project),
            "--task-type",
            "audit",
        ],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    hooks_path = project / ".codex" / "hooks.json"
    hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
    assert set(hooks["hooks"]) == {"SessionStart", "PreToolUse", "PostToolUse", "Stop"}
    assert "prove_it_ai_gate.codex_hooks" in hooks_path.read_text(encoding="utf-8")
    assert "open /hooks" in result.stdout
    row = status_rows()[0]
    assert row["status"] == "UNSUPERVISED"
    assert row["coverage"] == "HOOKS_CONFIGURED_UNTRUSTED_UNVERIFIED"
    assert row["codex_coverage"] == "HOOKS_CONFIGURED_UNTRUSTED_UNVERIFIED"
    assert (project / ".opencode" / "keep.txt").is_file()
    assert (project / "opencode.json").is_file()


def test_codex_transcript_cli_marks_registered_project_fallback_only(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AI_GATE_DAEMON_HOME", str(home))
    project = tmp_path / "project"
    project.mkdir()
    register_project(str(project), task_type="audit")
    session_path = tmp_path / "session.jsonl"
    session_path.write_text(json.dumps({
        "type": "session_meta",
        "payload": {
            "cwd": str(project),
            "timestamp": "2026-01-01T00:00:00Z",
            "cli_version": "test",
        },
    }) + "\n", encoding="utf-8")

    env = os.environ.copy()
    env["AI_GATE_DAEMON_HOME"] = str(home)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "prove_it_ai_gate.cli",
            "codex-transcript",
            "--project-dir",
            str(project),
            "--session",
            str(session_path),
        ],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    row = status_rows()[0]
    assert row["status"] == "FALLBACK_ONLY"
    assert row["codex_coverage"] == "FALLBACK_ONLY"
    metadata = json.loads(Path(row["latest_run_path"], "metadata.json").read_text(encoding="utf-8"))
    assert metadata["fallback_only"] is True


def test_codex_status_without_hooks_is_unsupervised(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AI_GATE_DAEMON_HOME", str(home))
    project = tmp_path / "project"
    project.mkdir()
    register_project(str(project), task_type="audit")

    row = status_rows()[0]
    assert row["codex_coverage"] == "UNSUPERVISED"


def test_daemon_logs_write_text_jsonl_and_redact_raw_output(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AI_GATE_DAEMON_HOME", str(home))

    write_daemon_log(
        "validation-result",
        project_dir="project",
        stdout="secret stdout",
        stderr="secret stderr",
        transcript_body="secret transcript",
    )

    text_lines = read_daemon_logs(tail=1)
    json_entries = read_daemon_logs(tail=1, json_output=True)
    assert text_lines
    assert json_entries[0]["event"] == "validation-result"
    assert "[redacted]" in text_lines[0]
    assert "secret stdout" not in text_lines[0]
    assert json_entries[0]["stdout"] == "[redacted]"

    env = os.environ.copy()
    env["AI_GATE_DAEMON_HOME"] = str(home)
    result = subprocess.run(
        [sys.executable, "-m", "prove_it_ai_gate.cli", "daemon", "logs", "--tail", "1"],
        env=env,
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "validation-result" in result.stdout
    assert "secret stdout" not in result.stdout


def test_daemon_owner_status_detects_stale_lock(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AI_GATE_DAEMON_HOME", str(home))
    home.mkdir()
    lock = home / "daemon.lock"
    lock.write_text(json.dumps({
        "pid": 99999999,
        "owner_id": "old",
        "heartbeat_at": "2020-01-01T00:00:00+00:00",
    }), encoding="utf-8")

    status = daemon_owner_status(home)
    assert status["lock_exists"] is True
    assert status["stale"] is True
    assert status["running"] is False


def test_windows_startup_install_status_is_idempotent(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows startup integration is Windows-only")
    appdata = tmp_path / "App Data"
    home = tmp_path / "Daemon Home"
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("AI_GATE_DAEMON_HOME", str(home))

    dry_run = install_windows_startup(dry_run=True)
    assert dry_run["dry_run"] is True
    assert dry_run["status"] == "missing"
    assert not Path(dry_run["path"]).exists()
    assert '"' in dry_run["expected_command"]

    installed = install_windows_startup()
    assert installed["status"] == "installed"
    assert installed["changed"] is True
    assert Path(installed["path"]).is_file()

    second = install_windows_startup()
    assert second["status"] == "installed"
    assert second["changed"] is False
    assert startup_status()["status"] == "installed"

    removed = uninstall_windows_startup()
    assert removed["changed"] is True
    removed_again = uninstall_windows_startup()
    assert removed_again["status"] == "absent"


def test_reports_list_index_and_open_from_metadata(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AI_GATE_DAEMON_HOME", str(home))
    project = tmp_path / "project"
    project.mkdir()
    _init_git_repo(project)

    builder = EvidenceRunBuilder.create(
        project_dir=str(project),
        evidence_root=str(project / "evidence"),
        task_type="audit",
        agent="test",
        session_id="report_ses",
    )
    metadata = builder.finalize(DEFAULT_POLICY_DIR, validation_timeout=1)
    assert metadata["acceptance_report_json"]

    rows, warnings = collect_reports(project_dir=str(project), limit=10)
    assert not warnings
    assert rows[0]["run_id"] == builder.run_id
    assert rows[0]["acceptance_report_json"] == metadata["acceptance_report_json"]

    index_json, index_html, index_warnings = build_report_index(str(project))
    assert not index_warnings
    assert index_json.is_file()
    assert index_html.is_file()
    assert builder.run_id in index_html.read_text(encoding="utf-8")

    opened: list[str] = []
    target = open_report_ref("latest", project_dir=str(project), opener=opened.append)
    assert opened == [target]
    assert target.endswith(".md") or target.endswith(".json") or Path(target).is_dir()


def test_daemon_status_json_has_normalized_fields(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AI_GATE_DAEMON_HOME", str(home))
    project = tmp_path / "project"
    project.mkdir()
    register_project(str(project), task_type="audit")

    env = os.environ.copy()
    env["AI_GATE_DAEMON_HOME"] = str(home)
    result = subprocess.run(
        [sys.executable, "-m", "prove_it_ai_gate.cli", "daemon", "status", "--json"],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    row = payload["projects"][0]
    assert row["state"] == "UNSUPERVISED"
    assert row["coverage"] == "UNSUPERVISED"
    assert row["next_action"]
    assert row["logs"].endswith("daemon.log")


def test_restart_recovery_is_idempotent_after_terminal_metadata(tmp_path):
    state_path = tmp_path / "state.json"
    project = tmp_path / "project"
    project.mkdir()
    _init_git_repo(project)
    db_path = tmp_path / "opencode.db"
    db = _make_opencode_db(db_path, project)
    _insert_tool(db, "ses_test", "git status --short")
    db.close()

    register_project(str(project), task_type="audit", state_path=state_path)
    daemon = SupervisorDaemon(
        DEFAULT_POLICY_DIR,
        state_path=state_path,
        opencode_db=str(db_path),
        idle_seconds=999,
        validation_timeout=1,
    )
    daemon.process_opencode()
    first_recovery = SupervisorDaemon(
        DEFAULT_POLICY_DIR,
        state_path=state_path,
        opencode_db=str(db_path),
        idle_seconds=999,
        validation_timeout=1,
    )
    first_recovery.recover_running_projects()
    row = status_rows(state_path, opencode_db=str(db_path))[0]
    run_path = Path(row["latest_run_path"])
    first_reports = list(run_path.glob("acceptance_report_*.json"))

    # Simulate a crash after terminal metadata was written but before current_run_path was cleared.
    state = json.loads(state_path.read_text(encoding="utf-8"))
    project_key = next(iter(state["projects"]))
    state["projects"][project_key]["current_run_path"] = str(run_path)
    state["projects"][project_key]["current_run_id"] = run_path.name
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    second_recovery = SupervisorDaemon(
        DEFAULT_POLICY_DIR,
        state_path=state_path,
        opencode_db=str(db_path),
        idle_seconds=999,
        validation_timeout=1,
    )
    second_recovery.recover_running_projects()
    second_reports = list(run_path.glob("acceptance_report_*.json"))
    assert len(second_reports) == len(first_reports) == 1


def test_codex_spool_checkpoint_skips_malformed_and_processes_once(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AI_GATE_DAEMON_HOME", str(home))
    project = tmp_path / "project"
    project.mkdir()
    _init_git_repo(project)
    register_project(str(project), task_type="audit")

    spool = home / "spool" / "codex_events.jsonl"
    spool.parent.mkdir(parents=True)
    events = [
        "{not-json\n",
        json.dumps({"hook_event_name": "SessionStart", "session_id": "codex_spool", "cwd": str(project), "event_id": "evt-start"}) + "\n",
        json.dumps({"hook_event_name": "Stop", "session_id": "codex_spool", "cwd": str(project), "event_id": "evt-stop"}) + "\n",
    ]
    spool.write_text("".join(events), encoding="utf-8")

    daemon = SupervisorDaemon(DEFAULT_POLICY_DIR, validation_timeout=1)
    daemon.process_codex_spool()
    daemon.process_codex_spool()

    row = status_rows()[0]
    run_path = Path(row["latest_run_path"])
    transcript_lines = [line for line in (run_path / "transcript.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(transcript_lines) == 2
    assert len(list(run_path.glob("acceptance_report_*.json"))) == 1
    logs = "\n".join(read_daemon_logs(tail=20))
    assert "malformed-json" in logs
