from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from src.evidence_retention import (
    find_evidence_folders,
    cleanup_evidence,
    archive_evidence,
    summary_report,
    _parse_age,
    _is_failure,
)


class TestAgeParsing:
    def test_parse_days(self):
        assert _parse_age("30d") == 30
        assert _parse_age("7d") == 7

    def test_parse_weeks(self):
        assert _parse_age("2w") == 14

    def test_parse_months(self):
        assert _parse_age("1m") == 30

    def test_parse_raw_number(self):
        assert _parse_age("45") == 45


class TestFindEvidenceFolders:
    def test_finds_folders_with_closeout(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            ev1 = root / "ev1"
            ev1.mkdir()
            (ev1 / "closeout.md").write_text("done")

            ev2 = root / "ev2"
            ev2.mkdir()
            (ev2 / "brief.md").write_text("brief")

            not_ev = root / "not_evidence"
            not_ev.mkdir()

            folders = find_evidence_folders(str(root), older_than_days=0)
            assert len(folders) >= 2

    def test_respects_age_filter(self):
        import time as _time
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            ev1 = root / "ev1"
            ev1.mkdir()
            (ev1 / "closeout.md").write_text("done")
            _time.sleep(0.1)

            folders_now = find_evidence_folders(str(root), older_than_days=0)
            assert len(folders_now) >= 1

            folders_far = find_evidence_folders(str(root), older_than_days=3650)
            assert len(folders_far) == 0


class TestIsFailure:
    def test_detects_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "acceptance_report_test.json").write_text(
                json.dumps({"decision": "REJECT"})
            )
            assert _is_failure(root) is True

    def test_detects_blocked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "acceptance_report_test.json").write_text(
                json.dumps({"decision": "BLOCKED"})
            )
            assert _is_failure(root) is True

    def test_accept_is_not_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "acceptance_report_test.json").write_text(
                json.dumps({"decision": "ACCEPT"})
            )
            assert _is_failure(root) is False


class TestCleanupEvidence:
    def test_dry_run_does_not_delete(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            ev = root / "ev1"
            ev.mkdir()
            (ev / "closeout.md").write_text("done")
            (ev / "acceptance_report_test.json").write_text(
                json.dumps({"decision": "ACCEPT"})
            )

            import os
            old_mtime = time.time() - (60 * 86400)
            os.utime(str(ev), (old_mtime, old_mtime))

            result = cleanup_evidence(str(root), older_than="30d", keep_failures=True, dry_run=True)
            assert result["folders_found"] >= 1
            assert ev.is_dir()

    def test_keep_failures_preserves_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            ev = root / "ev_rejected"
            ev.mkdir()
            (ev / "closeout.md").write_text("done")
            (ev / "acceptance_report_test.json").write_text(
                json.dumps({"decision": "REJECT"})
            )
            import os
            old_mtime = time.time() - (60 * 86400)
            os.utime(str(ev), (old_mtime, old_mtime))

            result = cleanup_evidence(str(root), older_than="30d", keep_failures=True, dry_run=True)
            assert result["folders_kept"] >= 1


class TestArchiveEvidence:
    def test_archives_folder(self, tmp_evidence):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = archive_evidence(tmp_evidence, tmpdir)
            assert path.is_dir()
            assert (path / "closeout.md").is_file()
            assert (path / "archive_manifest.json").is_file()


class TestSummaryReport:
    def test_reports_counts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            ev1 = root / "ev1"
            ev1.mkdir()
            (ev1 / "closeout.md").write_text("done")
            (ev1 / "acceptance_report_test.json").write_text(
                json.dumps({"decision": "ACCEPT"})
            )

            ev2 = root / "ev2"
            ev2.mkdir()
            (ev2 / "closeout.md").write_text("done")
            (ev2 / "acceptance_report_test.json").write_text(
                json.dumps({"decision": "REJECT"})
            )

            result = summary_report(str(root))
            assert result["total_folders"] >= 2
            assert result["failure_folders"] >= 1
            assert "total_size_mb" in result
