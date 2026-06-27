from __future__ import annotations

import html
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from .daemon_state import load_state, normalize_project_path, utc_now


def _read_json(path: Path) -> tuple[dict[str, Any] | None, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, f"missing: {path}"
    except json.JSONDecodeError as exc:
        return None, f"corrupt JSON: {path} ({exc})"
    except OSError as exc:
        return None, f"unreadable: {path} ({exc})"
    return (data if isinstance(data, dict) else None), ""


def _project_records(project_dir: str | None = None) -> list[dict[str, Any]]:
    state = load_state()
    if project_dir:
        key = normalize_project_path(project_dir)
        record = state.get("projects", {}).get(key)
        if record:
            return [record]
        resolved = str(Path(project_dir).expanduser().resolve())
        return [{
            "project_dir": resolved,
            "evidence_root": str(Path(resolved) / "evidence"),
            "task_type": "",
        }]
    return list(state.get("projects", {}).values())


def _latest_report_json(run_path: Path, metadata: dict[str, Any]) -> tuple[Path | None, dict[str, Any] | None, str]:
    candidate = metadata.get("acceptance_report_json")
    if not candidate:
        return None, None, f"missing acceptance report path in metadata: {run_path}"
    path = Path(candidate)
    if not path.is_absolute():
        path = run_path / path
    resolved_path = path
    try:
        resolved_run = run_path.resolve()
        resolved_path = path.resolve()
        is_inside_run = resolved_path == resolved_run or resolved_run in resolved_path.parents
    except OSError:
        is_inside_run = False
    if not is_inside_run or resolved_path.suffix.lower() != ".json":
        return None, None, f"unsafe acceptance report path in metadata: {path}"
    data, warning = _read_json(path)
    return path if data else None, data, warning


def collect_reports(
    project_dir: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    wanted = status.upper() if status else ""

    for record in _project_records(project_dir):
        evidence_root = Path(record.get("evidence_root") or Path(record.get("project_dir", "")) / "evidence")
        runs_root = evidence_root / "runs"
        if not runs_root.is_dir():
            continue
        for run_path in runs_root.iterdir():
            if not run_path.is_dir():
                continue
            metadata, warning = _read_json(run_path / "metadata.json")
            if warning:
                warnings.append(warning)
                continue
            if not metadata:
                warnings.append(f"metadata is not an object: {run_path / 'metadata.json'}")
                continue

            report_path, report, report_warning = _latest_report_json(run_path, metadata)
            if report_warning:
                warnings.append(report_warning)

            decision = str(metadata.get("decision") or metadata.get("status") or "")
            if report:
                decision = str(report.get("decision") or decision)
            state = decision or "RUNNING"
            if wanted and state != wanted:
                continue

            row = {
                "run_id": str(metadata.get("run_id") or run_path.name),
                "run_path": str(run_path),
                "project_dir": str(metadata.get("project_dir") or record.get("project_dir", "")),
                "evidence_root": str(evidence_root),
                "agent": str(metadata.get("agent") or ""),
                "session_id": str(metadata.get("session_id") or ""),
                "task_type": str(metadata.get("task_type") or record.get("task_type", "")),
                "state": state,
                "decision": decision,
                "created_at": str(metadata.get("created_at") or ""),
                "updated_at": str(metadata.get("updated_at") or ""),
                "finalized_at": str(metadata.get("finalized_at") or ""),
                "required_next_action": str(metadata.get("required_next_action") or ""),
                "late_snapshot": bool(metadata.get("late_snapshot")),
                "fallback_only": bool(metadata.get("fallback_only")),
                "acceptance_report_json": str(report_path or ""),
                "acceptance_report_md": str(metadata.get("acceptance_report_md") or ""),
                "warning": report_warning,
            }
            rows.append(row)

    rows.sort(key=lambda item: item.get("finalized_at") or item.get("updated_at") or item.get("created_at"), reverse=True)
    if limit > 0:
        rows = rows[:limit]
    return rows, warnings


def build_report_index(project_dir: str) -> tuple[Path, Path, list[str]]:
    records = _project_records(project_dir)
    if records:
        evidence_root = Path(records[0].get("evidence_root") or Path(project_dir) / "evidence")
    else:
        evidence_root = Path(project_dir).expanduser().resolve() / "evidence"

    rows, warnings = collect_reports(project_dir=project_dir, limit=0)
    reports_dir = evidence_root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    json_path = reports_dir / "index.json"
    html_path = reports_dir / "index.html"

    payload = {
        "schema": "ai-gate.report-index.v1",
        "generated_at": utc_now(),
        "project_dir": str(Path(project_dir).expanduser().resolve()),
        "evidence_root": str(evidence_root),
        "reports": rows,
        "warnings": warnings,
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    html_rows = []
    for row in rows:
        report_link = row["acceptance_report_md"] or row["acceptance_report_json"] or row["run_path"]
        html_rows.append(
            "<tr>"
            f"<td>{html.escape(row['state'])}</td>"
            f"<td>{html.escape(row['agent'])}</td>"
            f"<td>{html.escape(row['session_id'])}</td>"
            f"<td><a href=\"{html.escape(report_link)}\">{html.escape(row['run_id'])}</a></td>"
            f"<td>{html.escape(str(row['late_snapshot']).lower())}</td>"
            f"<td>{html.escape(row['required_next_action'] or '-')}</td>"
            "</tr>"
        )

    warning_html = "".join(f"<li>{html.escape(w)}</li>" for w in warnings)
    html_text = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>ai-gate reports</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; color: #1f2328; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border-bottom: 1px solid #d0d7de; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f6f8fa; }}
    code {{ background: #f6f8fa; padding: 2px 4px; }}
  </style>
</head>
<body>
  <h1>ai-gate reports</h1>
  <p>Project: <code>{html.escape(str(Path(project_dir).expanduser().resolve()))}</code></p>
  <table>
    <thead>
      <tr><th>State</th><th>Agent</th><th>Session</th><th>Run</th><th>Late Snapshot</th><th>Next Action</th></tr>
    </thead>
    <tbody>
      {''.join(html_rows)}
    </tbody>
  </table>
  {('<h2>Warnings</h2><ul>' + warning_html + '</ul>') if warnings else ''}
</body>
</html>
"""
    html_path.write_text(html_text, encoding="utf-8")
    return json_path, html_path, warnings


def resolve_report_ref(ref: str, project_dir: str | None = None) -> dict[str, Any]:
    raw = ref or "latest"
    candidate = Path(raw).expanduser()
    if candidate.exists():
        path = candidate.resolve()
        run_path = path if path.is_dir() else path.parent
        metadata, _warning = _read_json(run_path / "metadata.json")
        return {
            "run_path": str(run_path),
            "path": str(path),
            "metadata": metadata or {},
        }

    rows, _warnings = collect_reports(project_dir=project_dir, limit=0)
    if raw == "latest":
        if not rows:
            raise FileNotFoundError("No ai-gate reports found.")
        row = rows[0]
    else:
        matches = [row for row in rows if row["run_id"] == raw or Path(row["run_path"]).name == raw]
        if not matches:
            raise FileNotFoundError(f"No ai-gate report matches: {raw}")
        row = matches[0]
    return {
        "run_path": row["run_path"],
        "path": row["acceptance_report_md"] or row["acceptance_report_json"] or row["run_path"],
        "metadata": row,
    }


def open_report_ref(
    ref: str,
    project_dir: str | None = None,
    opener: Callable[[str], None] | None = None,
) -> str:
    resolved = resolve_report_ref(ref, project_dir=project_dir)
    target = resolved["path"]
    run_path = Path(resolved["run_path"])
    if not target or not Path(target).exists():
        target = str(run_path)

    if opener:
        opener(target)
        return target

    if os.name == "nt":
        os.startfile(target)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.run(["open", target], check=False)
    else:
        subprocess.run(["xdg-open", target], check=False)
    return target
