from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def project_root():
    return Path(__file__).resolve().parents[1]


@pytest.fixture
def policies_dir(project_root):
    return project_root / "prove_it_ai_gate" / "policies"


@pytest.fixture
def examples_dir(project_root):
    return project_root / "examples"


@pytest.fixture
def tmp_repo():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = Path(tmpdir) / "fake_project"
        repo.mkdir()
        src_dir = repo / "src"
        src_dir.mkdir()
        (src_dir / "main.py").write_text("print('hello')\n")
        os.system(f"git init {repo}")
        os.system(f'git -C {repo} config user.email "ci@example.com"')
        os.system(f'git -C {repo} config user.name "CI"')
        os.system(f"git -C {repo} add -A")
        os.system(f'git -C {repo} commit -m "init" --allow-empty')
        yield str(repo)


@pytest.fixture
def tmp_evidence():
    with tempfile.TemporaryDirectory() as tmpdir:
        evidence = Path(tmpdir) / "evidence"
        evidence.mkdir()

        (evidence / "brief.md").write_text("# Test Brief\n")
        (evidence / "plan.md").write_text("# Test Plan\n")
        (evidence / "commands.jsonl").write_text('{"command":"ls"}\n')
        (evidence / "changed_files.txt").write_text("src/main.py\n")
        (evidence / "closeout.md").write_text("# Closeout\nNo files changed.\n")
        (evidence / "risks.md").write_text("# Risks\nLow risk.\n")

        (evidence / "validation").mkdir()
        (evidence / "validation" / "test_output.txt").write_text("All tests passed.\n")
        (evidence / "validation" / "lint_output.txt").write_text("No issues.\n")
        (evidence / "validation" / "typecheck_output.txt").write_text("0 errors.\n")
        (evidence / "validation" / "audit_guard.txt").write_text("Audit guard passed.\n")

        (evidence / "artifacts").mkdir()
        (evidence / "artifacts" / "inventory.json").write_text(json.dumps({"files": [{"path": "src/main.py", "size_bytes": 20}], "total_files": 1}))
        (evidence / "artifacts" / "extracted_findings.json").write_text(json.dumps({"findings": ["test"]}))

        (evidence / "workspace").mkdir()
        (evidence / "workspace" / "git_status_before.txt").write_text("clean")
        (evidence / "workspace" / "git_status_after.txt").write_text("")

        (evidence / "transcript.jsonl").write_text(json.dumps({"type": "tool_result", "content": "ok"}) + "\n")
        (evidence / "workspace" / "diff_summary.patch").write_text("diff --git a/src/main.py b/src/main.py\n")
        (evidence / "validation_summary.md").write_text("# Validation Summary\nPassed.\n")

        yield str(evidence)


@pytest.fixture
def tmp_transcript():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as fh:
        events = [
            {"timestamp": "2026-06-26T10:00:00Z", "type": "tool_result", "role": "tool", "tool_name": "read", "content": "File content here."},
            {"timestamp": "2026-06-26T10:00:05Z", "type": "tool_result", "role": "tool", "tool_name": "shell", "command": "git status --short", "stdout": "", "exit_code": 0},
            {"timestamp": "2026-06-26T10:00:10Z", "type": "message", "role": "assistant", "content": "The audit is complete."},
        ]
        for event in events:
            fh.write(json.dumps(event) + "\n")
        path = fh.name
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def truncated_transcript():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as fh:
        events = [
            {"timestamp": "2026-06-26T10:00:00Z", "type": "tool_result", "role": "tool", "tool_name": "shell",
             "command": "grep -r TODO src/", "stdout": "Results:\nsrc/main.py:5:  # TODO\n... truncated ...\nshowing first 50 of 500 results", "exit_code": 0},
            {"timestamp": "2026-06-26T10:00:10Z", "type": "message", "role": "assistant", "content": "Found all TODO items. Complete."},
        ]
        for event in events:
            fh.write(json.dumps(event) + "\n")
        path = fh.name
    yield path
    Path(path).unlink(missing_ok=True)
