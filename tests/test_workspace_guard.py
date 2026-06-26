from __future__ import annotations

import os
import tempfile
from pathlib import Path

from src.workspace_guard import check_workspace_hygiene, parse_status_entries, run_git_status


class TestGitStatusParser:
    def test_parse_modified(self):
        entries = parse_status_entries(" M src/main.py")
        assert entries["modified"] == ["src/main.py"]

    def test_parse_added(self):
        entries = parse_status_entries("A  newfile.py")
        assert entries["added"] == ["newfile.py"]

    def test_parse_deleted(self):
        entries = parse_status_entries(" D oldfile.py")
        assert entries["deleted"] == ["oldfile.py"]

    def test_parse_untracked(self):
        entries = parse_status_entries("?? scratch_test.py")
        assert entries["untracked"] == ["scratch_test.py"]

    def test_parse_mixed(self):
        output = " M src/main.py\nA  new.py\n?? tmp.py"
        entries = parse_status_entries(output)
        assert "src/main.py" in entries["modified"]
        assert "new.py" in entries["added"]
        assert "tmp.py" in entries["untracked"]


class TestWorkspaceHygiene:
    def test_read_only_task_clean_workspace_passes(self, tmp_repo, tmp_evidence):
        result = check_workspace_hygiene(tmp_repo, "audit", str(tmp_evidence))
        assert result.status in ("passed", "warning")

    def test_read_only_task_with_changes_rejects(self, tmp_repo, tmp_evidence):
        evidence_dir = Path(tmp_evidence)
        ws_dir = evidence_dir / "workspace"
        (ws_dir / "git_status_before.txt").write_text("")
        (ws_dir / "git_status_after.txt").write_text(" M src/main.py")
        result = check_workspace_hygiene(tmp_repo, "audit", str(evidence_dir))
        assert result.status == "failed"

    def test_closeout_claims_no_changes_but_status_shows_changes(self, tmp_repo, tmp_evidence):
        evidence_dir = Path(tmp_evidence)
        (evidence_dir / "closeout.md").write_text("No files changed. Audit complete.")
        ws_dir = evidence_dir / "workspace"
        (ws_dir / "git_status_before.txt").write_text("")
        (ws_dir / "git_status_after.txt").write_text(" M src/main.py")
        result = check_workspace_hygiene(tmp_repo, "audit", str(evidence_dir))
        assert result.status == "failed"
        assert any("closeout" in i.message.lower() for i in result.issues)

    def test_code_change_task_allows_changes(self, tmp_repo, tmp_evidence):
        evidence_dir = Path(tmp_evidence)
        ws_dir = evidence_dir / "workspace"
        (ws_dir / "git_status_before.txt").write_text("")
        (ws_dir / "git_status_after.txt").write_text(" M src/main.py")
        result = check_workspace_hygiene(tmp_repo, "code_change", str(evidence_dir))
        assert result.status in ("passed", "warning")

    def test_scratch_files_flag_warning(self, tmp_repo, tmp_evidence):
        evidence_dir = Path(tmp_evidence)
        ws_dir = evidence_dir / "workspace"
        (ws_dir / "git_status_before.txt").write_text("")
        (ws_dir / "git_status_after.txt").write_text("?? scratch_test.py")
        result = check_workspace_hygiene(tmp_repo, "code_change", str(evidence_dir))
        assert any("scratch" in i.message.lower() for i in result.issues)
