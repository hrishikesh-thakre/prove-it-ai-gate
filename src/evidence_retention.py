from __future__ import annotations

import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path


def _parse_age(age_str: str) -> int:
    age_str = age_str.strip().lower()
    if age_str.endswith("d"):
        return int(age_str[:-1])
    if age_str.endswith("w"):
        return int(age_str[:-1]) * 7
    if age_str.endswith("m"):
        return int(age_str[:-1]) * 30
    return int(age_str)


def find_evidence_folders(root: str, older_than_days: int) -> list[Path]:
    root_path = Path(root)
    if not root_path.is_dir():
        return []

    cutoff = time.time() - (older_than_days * 86400)
    folders: list[Path] = []

    for entry in root_path.iterdir():
        if not entry.is_dir():
            continue
        if entry.name.startswith("."):
            continue

        has_evidence_marker = (
            (entry / "closeout.md").is_file()
            or (entry / "brief.md").is_file()
            or (entry / "commands.jsonl").is_file()
        )
        if not has_evidence_marker:
            continue

        mtime = entry.stat().st_mtime
        if mtime <= cutoff:
            folders.append(entry)

    return sorted(folders, key=lambda p: p.stat().st_mtime)


def _is_failure(evidence_dir: Path) -> bool:
    report_files = sorted(evidence_dir.glob("acceptance_report_*.json"), reverse=True)
    if not report_files:
        return True

    try:
        import json
        data = json.loads(report_files[0].read_text(encoding="utf-8"))
        return data.get("decision") in ("REJECT", "BLOCKED")
    except Exception:
        return True


def cleanup_evidence(root: str, older_than: str, keep_failures: bool = True, dry_run: bool = True) -> dict:
    days = _parse_age(older_than)
    folders = find_evidence_folders(root, days)

    result = {
        "root": root,
        "older_than_days": days,
        "folders_found": len(folders),
        "folders_removed": 0,
        "folders_kept": 0,
        "removed": [],
        "kept": [],
    }

    for folder in folders:
        is_fail = _is_failure(folder)
        if keep_failures and is_fail:
            result["folders_kept"] += 1
            result["kept"].append({"path": str(folder), "reason": "failure_or_blocked"})
            continue

        result["folders_removed"] += 1
        result["removed"].append({"path": str(folder)})
        if not dry_run:
            shutil.rmtree(folder, ignore_errors=True)

    return result


def archive_evidence(evidence_path: str, output_dir: str, name: str | None = None) -> Path:
    evidence = Path(evidence_path)
    if not evidence.is_dir():
        raise FileNotFoundError(f"Evidence folder not found: {evidence_path}")

    archive_root = Path(output_dir)
    archive_root.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    archive_name = name or f"evidence_{timestamp}"
    archive_path = archive_root / archive_name

    if archive_path.exists():
        archive_path = archive_root / f"{archive_name}_{timestamp}"

    shutil.copytree(evidence, archive_path)

    manifest = {
        "archived_at": datetime.now().isoformat(timespec="seconds"),
        "source": str(evidence),
        "archive": str(archive_path),
        "name": archive_name,
    }

    import json
    (archive_path / "archive_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return archive_path


def summary_report(root: str) -> dict:
    root_path = Path(root)
    if not root_path.is_dir():
        return {"error": f"Not a directory: {root}"}

    total_size = 0
    total_folders = 0
    failure_folders = 0

    for entry in root_path.iterdir():
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if not ((entry / "closeout.md").is_file() or (entry / "brief.md").is_file()):
            continue

        total_folders += 1
        try:
            total_size += sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
        except Exception:
            pass

        if _is_failure(entry):
            failure_folders += 1

    return {
        "root": root,
        "total_folders": total_folders,
        "failure_folders": failure_folders,
        "clean_folders": total_folders - failure_folders,
        "total_size_bytes": total_size,
        "total_size_mb": round(total_size / (1024 * 1024), 2),
    }
