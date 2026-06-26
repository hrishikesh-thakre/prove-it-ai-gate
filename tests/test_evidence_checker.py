from __future__ import annotations

import json
import tempfile
from pathlib import Path

from src.evidence_checker import check_evidence_folder, inventory_evidence_files


class TestEvidenceFolder:
    def test_valid_evidence_passes(self, tmp_evidence):
        result = check_evidence_folder(tmp_evidence, "audit")
        assert result.status == "passed"

    def test_missing_evidence_folder(self):
        result = check_evidence_folder("/nonexistent/evidence", "audit")
        assert result.status == "blocked"

    def test_empty_evidence_file_detected(self, tmp_evidence):
        evidence = Path(tmp_evidence)
        (evidence / "changed_files.txt").write_text("")
        result = check_evidence_folder(tmp_evidence, "code_change")
        assert result.status == "blocked"
        assert any("empty" in i.message.lower() for i in result.issues)

    def test_placeholder_content_detected(self, tmp_evidence):
        evidence = Path(tmp_evidence)
        (evidence / "changed_files.txt").write_text("TODO: add changed files list later")
        result = check_evidence_folder(tmp_evidence, "code_change")
        assert result.status == "blocked"

    def test_code_change_requires_changed_files(self, tmp_evidence):
        evidence = Path(tmp_evidence)
        (evidence / "changed_files.txt").unlink()
        result = check_evidence_folder(tmp_evidence, "code_change")
        assert result.status == "blocked"
        assert any("changed_files" in i.message.lower() for i in result.issues)

    def test_audit_requires_inventory(self, tmp_evidence):
        evidence = Path(tmp_evidence)
        (evidence / "artifacts" / "inventory.json").unlink()
        result = check_evidence_folder(tmp_evidence, "audit")
        assert result.status == "blocked"


class TestInventoryEvidence:
    def test_inventory_lists_files(self, tmp_evidence):
        data = inventory_evidence_files(tmp_evidence)
        assert data["file_count"] > 0
        assert "files" in data

    def test_inventory_on_nonexistent_path(self):
        data = inventory_evidence_files("/nonexistent")
        assert "error" in data
