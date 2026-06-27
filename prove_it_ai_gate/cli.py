from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .gate_engine import Decision, run_acceptance
from .transcript_checker import check_transcript_truncation
from .workspace_guard import check_workspace_hygiene
from .evidence_checker import check_evidence_folder
from .confidence_checker import check_confidence_claims
from .scope_completeness_checker import check_scope_completeness
from .heuristic_checker import check_heuristic_extraction
from .acceptance_report import write_report, write_csv_report
from .knowledge_capture import write_capture, capture_from_json_report, upload_to_wiki
from .evidence_retention import cleanup_evidence, archive_evidence, summary_report
from .desktop_watcher import start_watcher
from .codex_transcript import extract_transcript
from .daemon import (
    SupervisorDaemon,
    check_opencode_compatibility,
    install_windows_startup,
    request_stop,
    start_background,
    startup_status,
    status_rows,
    uninstall_windows_startup,
)
from .daemon_log import daemon_log_paths, read_daemon_logs, write_daemon_log
from .daemon_state import (
    find_project_for_cwd,
    get_state_path,
    list_projects,
    load_state,
    normalize_project_path,
    register_project,
    save_state,
    unregister_project,
    utc_now,
)
from .report_browser import build_report_index, collect_reports, open_report_ref, resolve_report_ref


DEFAULT_POLICY_DIR = os.path.join(os.path.dirname(__file__), "policies")


def _env_or_arg(env_name: str, arg_value: str | None) -> str:
    return arg_value or os.environ.get(env_name, "")


def cmd_init(args: argparse.Namespace) -> int:
    print("Initialized prove-it-ai-gate project.")
    print()
    print("Next steps:")
    print("  1. Run your agent and save the transcript as transcript.jsonl")
    print("  2. Collect evidence in an evidence/ folder")
    print("  3. Run: ai-gate accept --repo . --evidence ./evidence --transcript transcript.jsonl")
    return 0


def cmd_transcript_check(args: argparse.Namespace) -> int:
    result = check_transcript_truncation(args.transcript)
    print(f"Transcript truncation check: {result.status.upper()}")
    for issue in result.issues:
        print(f"  [{issue.severity.value}] {issue.message}")
    if result.details:
        print(f"  Total events: {result.details.get('total_events', 0)}")
        print(f"  Truncated steps: {len(result.details.get('truncated_steps', []))}")
    return 0 if result.status == "passed" else 1


def cmd_workspace_check(args: argparse.Namespace) -> int:
    result = check_workspace_hygiene(
        args.repo, args.task_type or "audit",
        evidence_path=getattr(args, "evidence", ""),
        scratch_dir=getattr(args, "scratch_dir", ""),
    )
    print(f"Workspace hygiene check: {result.status.upper()}")
    for issue in result.issues:
        print(f"  [{issue.severity.value}] {issue.message}")
    return 0 if result.status == "passed" else 1


def cmd_evidence_check(args: argparse.Namespace) -> int:
    result = check_evidence_folder(args.evidence, args.task_type or "audit")
    print(f"Evidence folder check: {result.status.upper()}")
    for issue in result.issues:
        print(f"  [{issue.severity.value}] {issue.message}")
    return 0 if result.status == "passed" else 1


def cmd_accept(args: argparse.Namespace) -> int:
    policy_dir = args.policy_dir or DEFAULT_POLICY_DIR
    report = run_acceptance(
        repo_path=args.repo,
        evidence_path=args.evidence,
        transcript_path=args.transcript,
        policy_dir=policy_dir,
        policy_name=args.policy,
        task_type=args.task_type,
        scratch_dir=getattr(args, "scratch_dir", ""),
    )

    md_path, json_path = write_report(report, args.output or ".")
    write_csv_report(report, args.output or ".")

    print(f"Decision: {report.decision.value}")
    print()
    print("Check results:")
    for check in report.checks:
        issues_count = len(check.issues)
        print(f"  {check.check_name}: {check.status.upper()}" +
              (f" ({issues_count} issue(s))" if issues_count else ""))

    print()
    if report.failed_checks:
        print(f"Failed checks: {', '.join(report.failed_checks)}")
    if report.blocked_checks:
        print(f"Blocked checks: {', '.join(report.blocked_checks)}")
    if report.warning_checks:
        print(f"Checks with warnings: {', '.join(report.warning_checks)}")

    print()
    print(f"Required next action: {report.required_next_action}")
    print(f"Reports written to: {md_path}")
    print(f"                  {json_path}")

    if report.decision == Decision.ACCEPT:
        return 0
    elif report.decision == Decision.ACCEPT_WITH_CONDITIONS:
        return 2
    elif report.decision == Decision.REJECT:
        return 1
    else:
        return 3


def cmd_capture(args: argparse.Namespace) -> int:
    if args.report_json:
        path = capture_from_json_report(args.report_json, args.evidence or ".", args.output or ".")
        print(f"Knowledge capture written from report: {path}")
        if args.upload:
            return _do_upload(path, args)
        return 0

    policy_dir = args.policy_dir or DEFAULT_POLICY_DIR
    report = run_acceptance(
        repo_path=args.repo,
        evidence_path=args.evidence,
        transcript_path=args.transcript,
        policy_dir=policy_dir,
        policy_name=args.policy,
        task_type=args.task_type,
        scratch_dir=getattr(args, "scratch_dir", ""),
    )

    capture_path = write_capture(report, args.evidence, args.output or ".")
    print(f"Decision: {report.decision.value}")
    print(f"Knowledge capture written to: {capture_path}")
    if args.upload:
        return _do_upload(str(capture_path), args)
    return 0


def _do_upload(capture_path: str, args: argparse.Namespace) -> int:
    wiki_url = _env_or_arg("AI_GATE_WIKI_URL", args.wiki_url)
    token_id = _env_or_arg("AI_GATE_WIKI_TOKEN_ID", args.wiki_token_id)
    token_secret = _env_or_arg("AI_GATE_WIKI_TOKEN_SECRET", args.wiki_token_secret)

    if not wiki_url or not token_id or not token_secret:
        print("Upload requires wiki API credentials.")
        return 1

    try:
        result = upload_to_wiki(capture_path, wiki_url, token_id, token_secret)
        print(f"Uploaded to wiki: {result['title']} ({result['status']})")
        return 0
    except Exception as exc:
        print(f"Upload failed: {exc}")
        return 1


def cmd_cleanup(args: argparse.Namespace) -> int:
    dry_run = not getattr(args, "apply", False)
    result = cleanup_evidence(
        root=args.evidence_root,
        older_than=args.older_than,
        keep_failures=not getattr(args, "clean_all", False),
        dry_run=dry_run,
    )

    print(f"Evidence cleanup ({'DRY RUN' if dry_run else 'APPLYING'}):")
    print(f"  Root: {result['root']}")
    print(f"  Older than: {result['older_than_days']} days")
    print(f"  Folders found: {result['folders_found']}")
    print(f"  To remove: {result['folders_removed']}")
    print(f"  Kept (failures): {result['folders_kept']}")

    if result["removed"]:
        print("\n  Would remove:" if dry_run else "\n  Removed:")
        for item in result["removed"]:
            print(f"    - {item['path']}")

    if dry_run:
        print("\n  Use --apply to execute cleanup.")
    return 0


def cmd_archive(args: argparse.Namespace) -> int:
    path = archive_evidence(args.evidence, args.output or ".", name=args.name)
    print(f"Evidence archived to: {path}")
    return 0


def cmd_evidence_summary(args: argparse.Namespace) -> int:
    result = summary_report(args.evidence_root)
    if "error" in result:
        print(result["error"])
        return 1
    print(f"Evidence summary for: {result['root']}")
    print(f"  Total folders: {result['total_folders']}")
    print(f"  Failure/blocked folders: {result['failure_folders']}")
    print(f"  Clean folders: {result['clean_folders']}")
    print(f"  Total size: {result['total_size_mb']} MB")
    return 0


def cmd_reuse_scan(args: argparse.Namespace) -> int:
    from .reuse_scout import scan_reuse_wiki, format_reuse_report

    wiki_url = _env_or_arg("AI_GATE_WIKI_URL", args.wiki_url)
    token_id = _env_or_arg("AI_GATE_WIKI_TOKEN_ID", args.wiki_token_id)
    token_secret = _env_or_arg("AI_GATE_WIKI_TOKEN_SECRET", args.wiki_token_secret)

    if not wiki_url or not token_id or not token_secret:
        print("Reuse Scout requires wiki API credentials for live mode.")
        print("Set AI_GATE_WIKI_URL, AI_GATE_WIKI_TOKEN_ID, AI_GATE_WIKI_TOKEN_SECRET")
        print("or pass --wiki-url, --wiki-token-id, --wiki-token-secret")
        print()
        print("Alternatively, run with --local-wiki to scan a local wiki export:")
        print("  ai-gate reuse-scan --brief brief.md --local-wiki ./wiki-exports")
        return 1

    print(f"Scanning reuse wiki at {wiki_url} ...")
    try:
        result = scan_reuse_wiki(
            brief_path=args.brief,
            wiki_url=wiki_url,
            wiki_token_id=token_id,
            wiki_token_secret=token_secret,
            max_results=args.max_results or 30,
        )
    except Exception as exc:
        print(f"Reuse scan failed: {exc}")
        return 1

    report_md = format_reuse_report(result)
    output = args.output or "."
    out_dir = Path(output)
    out_dir.mkdir(parents=True, exist_ok=True)

    md_path = out_dir / "reuse_scan_report.md"
    md_path.write_text(report_md, encoding="utf-8")

    json_path = out_dir / "reuse_scan_report.json"
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Found {result['totals']['results_returned']} matches out of "
          f"{result['totals']['canonical_pages_available']} canonical pages.")
    print(f"Reports written to: {md_path}")
    print(f"                  {json_path}")
    return 0


def cmd_reuse_scan_local(args: argparse.Namespace) -> int:
    from .reuse_scout import format_reuse_report, _word_set, _score_match, _classify_reuse, _parse_front_matter, _clean_list, _jaccard
    from datetime import datetime

    wiki_path = Path(args.local_wiki)
    if not wiki_path.is_dir():
        print(f"Local wiki path not found: {wiki_path}")
        return 1

    brief_text = Path(args.brief).read_text(encoding="utf-8", errors="replace")
    brief_words = _word_set(brief_text)

    md_files = list(wiki_path.rglob("*.md"))
    scored_pages: list[dict] = []

    for md_file in md_files:
        try:
            content = md_file.read_text(encoding="utf-8", errors="replace")
            fm = _parse_front_matter(content)

            if not fm.get("type"):
                continue

            page = {
                "id": str(md_file.stem),
                "name": fm.get("title", md_file.stem.replace("-", " ").title()),
                "book": md_file.parent.name,
                "type": fm.get("type", ""),
                "status": fm.get("status", ""),
                "maturity": fm.get("maturity", ""),
                "reuse_status": fm.get("reuse_status", ""),
                "owner": fm.get("owner", ""),
                "tags": _clean_list(fm.get("tags", "")),
                "related_projects": _clean_list(fm.get("related_projects", "")),
                "last_validated": fm.get("last_validated", ""),
            }

            body = content.split("---", 2)[-1] if content.count("---") >= 2 else content

            enriched = {
                **page,
                "relevance_score": _score_match(brief_words, page, brief_text, body),
                "body_overlap": round(_jaccard(brief_words, _word_set(body)) if brief_words and _word_set(body) else 0.0, 3),
                "reuse_classification": _classify_reuse(page),
                "needs_human_review": page["reuse_status"] == "needs_human_review" or page["status"] == "draft",
            }
            scored_pages.append(enriched)
        except Exception:
            continue

    scored_pages.sort(key=lambda p: (-p["relevance_score"], p["name"]))
    max_results = args.max_results or 30
    top_pages = scored_pages[:max_results]

    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "brief_path": str(args.brief),
        "brief_summary": brief_text[:200].replace("\n", " "),
        "wiki_url": f"local:{wiki_path}",
        "totals": {
            "canonical_pages_available": len(md_files),
            "pages_fetched": len(scored_pages),
            "results_returned": len(top_pages),
        },
        "counts": {
            "by_type": {},
            "by_book": {},
            "by_reuse_classification": {},
        },
        "results": top_pages,
    }

    from collections import Counter
    result["counts"]["by_type"] = dict(Counter(p["type"] for p in top_pages))
    result["counts"]["by_reuse_classification"] = dict(Counter(p["reuse_classification"] for p in top_pages))

    report_md = format_reuse_report(result)
    output = args.output or "."
    out_dir = Path(output)
    out_dir.mkdir(parents=True, exist_ok=True)

    md_path = out_dir / "reuse_scan_report.md"
    md_path.write_text(report_md, encoding="utf-8")

    json_path = out_dir / "reuse_scan_report.json"
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Found {result['totals']['results_returned']} matches out of "
          f"{result['totals']['pages_fetched']} local wiki pages.")
    print(f"Reports written to: {md_path}")
    print(f"                  {json_path}")
    return 0


def cmd_opencode_watch(args: argparse.Namespace) -> int:
    """Live watcher: poll OpenCode SQLite DB, detect violations, alert."""
    start_watcher(
        project_dir=args.project_dir or ".",
        task_type=args.task_type or "audit",
        poll_interval=args.interval,
        auto_terminate=args.auto_terminate,
    )
    return 0


def cmd_setup_codex(args: argparse.Namespace) -> int:
    """Install Codex hook-first ai-gate capture for this project."""
    project_dir = Path(args.project_dir or ".").resolve()
    task_type = args.task_type or "audit"
    hooks_dir = project_dir / ".codex"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    hook_cmd = f'"{sys.executable}" -m prove_it_ai_gate.codex_hooks'
    hook_handler = {
        "type": "command",
        "command": hook_cmd,
        "commandWindows": hook_cmd,
        "timeout": 30,
        "statusMessage": "Capturing ai-gate evidence",
    }
    hooks = {
        "hooks": {
            "SessionStart": [{
                "matcher": "startup|resume|clear|compact",
                "hooks": [dict(hook_handler, statusMessage="Starting ai-gate evidence run")],
            }],
            "PreToolUse": [{
                "matcher": "*",
                "hooks": [dict(hook_handler, statusMessage="Checking ai-gate policy")],
            }],
            "PostToolUse": [{
                "matcher": "*",
                "hooks": [dict(hook_handler, statusMessage="Capturing ai-gate tool output")],
            }],
            "Stop": [{
                "hooks": [dict(hook_handler, statusMessage="Finalizing ai-gate evidence")],
            }],
        }
    }

    hooks_path = hooks_dir / "hooks.json"
    hooks_path.write_text(json.dumps(hooks, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    record = register_project(str(project_dir), task_type=task_type)

    print("Codex ai-gate hooks installed.")
    print()
    print(f"  Hooks:      {hooks_path}")
    print(f"  State:      {get_state_path()}")
    print(f"  Project:    {record['project_dir']}")
    print(f"  Task type:  {record['task_type']}")
    print()
    print("Limitations:")
    print("  - Codex must review and trust non-managed hooks before they run.")
    print("  - Hook events are the primary evidence source; transcript parsing is fallback only.")
    print("  - If hooks are configured but not observed, daemon status reports Codex as HOOKS_CONFIGURED_UNTRUSTED_UNVERIFIED.")
    print()
    print("Restart Codex or start a new session, then open /hooks to review and trust this project hook once.")
    return 0


def cmd_setup_daemon(args: argparse.Namespace) -> int:
    project_dir = Path(args.project_dir).resolve()
    record = register_project(
        project_dir=str(project_dir),
        task_type=args.task_type or "audit",
        evidence_root=getattr(args, "evidence_root", None),
        policy=getattr(args, "policy", "") or "",
    )
    print("Daemon supervision configured.")
    print(f"  Project:    {record['project_dir']}")
    print(f"  Task type:  {record['task_type']}")
    print(f"  Evidence:   {record['evidence_root']}")
    print(f"  State:      {get_state_path()}")
    write_daemon_log(
        "project-registered",
        project_dir=record["project_dir"],
        task_type=record["task_type"],
        evidence_root=record["evidence_root"],
    )

    if args.install_startup:
        try:
            result = install_windows_startup(force=getattr(args, "force", False), dry_run=False)
        except RuntimeError as exc:
            print(f"Startup install skipped: {exc}")
            return 1
        print(f"  Startup:    {result['status']} ({result['path']})")
    print()
    print("Next: run ai-gate daemon start, or rely on Windows startup if installed.")
    return 0


def _print_status_rows(rows: list[dict]) -> None:
    for row in rows:
        print(f"{row['state']:24} {row['project_dir']}")
        print(f"  coverage:      {row.get('coverage') or '-'}")
        print(f"  agent/session: {row['agent'] or '-'} / {row['session_id'] or '-'}")
        print(f"  run:           {row['latest_run_path'] or '-'}")
        print(f"  decision:      {row['decision'] or '-'}")
        print(f"  late snapshot: {str(row.get('late_snapshot', False)).lower()}")
        if row.get("idle_for_seconds") is not None:
            print(f"  idle:          {row['idle_for_seconds']}s since last OpenCode event")
        if row.get("finalize_reason"):
            print(f"  finalized by:  {row['finalize_reason']}")
        print(f"  next action:   {row['required_next_action'] or '-'}")
        print(f"  logs:          {row.get('logs') or '-'}")
        print(f"  opencode db:   {row.get('opencode_db_status', 'unknown')} ({row.get('opencode_db_path', '-')})")
        if row.get("opencode_db_message"):
            print(f"  opencode note: {row['opencode_db_message']}")
        if row.get("opencode_sample_tool_shape"):
            print(f"  tool sample:   {row['opencode_sample_tool_shape']}")
        if row.get("codex_coverage"):
            print(f"  codex:         {row['codex_coverage']}")


def cmd_daemon(args: argparse.Namespace) -> int:
    action = getattr(args, "daemon_command", None)

    if action == "register":
        record = register_project(
            project_dir=args.project_dir,
            task_type=args.task_type or "audit",
            evidence_root=args.evidence_root,
            policy=args.policy or "",
        )
        print(f"Registered: {record['project_dir']}")
        print(f"  task_type: {record['task_type']}")
        print(f"  evidence:  {record['evidence_root']}")
        print(f"  state:     {get_state_path()}")
        write_daemon_log(
            "project-registered",
            project_dir=record["project_dir"],
            task_type=record["task_type"],
            evidence_root=record["evidence_root"],
        )
        return 0

    if action == "unregister":
        removed = unregister_project(args.project_dir)
        key = normalize_project_path(args.project_dir)
        print(("Unregistered: " if removed else "Not registered: ") + key)
        write_daemon_log("project-unregistered", project_dir=key, removed=removed)
        return 0 if removed else 1

    if action == "list":
        projects = list_projects()
        if not projects:
            print("No registered projects.")
            return 0
        for record in projects:
            print(f"{record['project_dir']}")
            print(f"  task_type: {record.get('task_type', 'audit')}")
            print(f"  evidence:  {record.get('evidence_root', '')}")
            print(f"  agents:    {', '.join(record.get('agents', []))}")
        return 0

    if action == "start":
        if args.foreground:
            daemon = SupervisorDaemon(
                policy_dir=args.policy_dir or DEFAULT_POLICY_DIR,
                poll_interval=args.interval,
                idle_seconds=args.idle_seconds,
                validation_timeout=args.validation_timeout,
            )
            print(f"Starting ai-gate daemon in foreground. State: {get_state_path()}")
            try:
                daemon.run_foreground(recover=not args.no_recover)
            except RuntimeError as exc:
                print(str(exc))
                return 1
            return 0
        pid = start_background(
            interval=args.interval,
            idle_seconds=args.idle_seconds,
            validation_timeout=args.validation_timeout,
            policy_dir=args.policy_dir or DEFAULT_POLICY_DIR,
            recover=not args.no_recover,
        )
        print(f"Starting ai-gate daemon in background (pid {pid}).")
        print(f"State: {get_state_path()}")
        return 0

    if action == "stop":
        path = request_stop()
        print(f"Stop requested: {path}")
        return 0

    if action == "status":
        rows = status_rows()
        if getattr(args, "json", False):
            print(json.dumps({"projects": rows}, indent=2, ensure_ascii=False))
            return 0
        if not rows:
            print("No registered projects.")
            health = check_opencode_compatibility()
            print(f"OpenCode DB: {health.get('status', 'unknown')} ({health.get('path', '-')})")
            if health.get("message"):
                print(f"OpenCode note: {health['message']}")
            if health.get("sample_tool_shape"):
                print(f"Tool sample: {health['sample_tool_shape']}")
            print(f"Logs: {daemon_log_paths()['text']}")
            return 0
        _print_status_rows(rows)
        return 0

    if action == "install-startup":
        try:
            result = install_windows_startup(force=args.force, dry_run=args.dry_run)
        except RuntimeError as exc:
            print(str(exc))
            return 1
        print(f"Startup status: {result['status']}")
        print(f"  path:    {result['path']}")
        print(f"  log:     {result.get('log_path', daemon_log_paths()['text'])}")
        print(f"  changed: {str(result.get('changed', False)).lower()}")
        if result.get("dry_run"):
            print("  dry_run: true")
            print("  command:")
            print(result["expected_command"].rstrip())
        elif result["status"] == "stale_command" and not result.get("changed"):
            print("Existing startup command differs. Re-run with --force to replace it.")
            return 1
        return 0

    if action == "startup-status":
        try:
            result = startup_status()
        except RuntimeError as exc:
            print(str(exc))
            return 1
        if getattr(args, "json", False):
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        print(f"Startup status: {result['status']}")
        print(f"  path: {result['path']}")
        print(f"  log:  {result['log_path']}")
        return 0

    if action == "uninstall-startup":
        try:
            result = uninstall_windows_startup()
        except RuntimeError as exc:
            print(str(exc))
            return 1
        print(f"Startup status: {result['status']}")
        print(f"  path:    {result['path']}")
        print(f"  changed: {str(result.get('changed', False)).lower()}")
        return 0

    if action == "logs":
        entries = read_daemon_logs(tail=args.tail, json_output=args.json)
        if args.json:
            print(json.dumps(entries, indent=2, ensure_ascii=False))
        else:
            for line in entries:
                print(line)
        return 0

    print("Missing daemon subcommand.")
    return 1


def cmd_reports(args: argparse.Namespace) -> int:
    action = getattr(args, "reports_command", None)

    if action == "list":
        rows, warnings = collect_reports(
            project_dir=args.project_dir,
            status=args.status,
            limit=args.limit,
        )
        if args.json:
            print(json.dumps({"reports": rows, "warnings": warnings}, indent=2, ensure_ascii=False))
            return 0
        if not rows:
            print("No ai-gate reports found.")
        for row in rows:
            print(f"{row['state']:24} {row['run_id']}")
            print(f"  project: {row['project_dir']}")
            print(f"  agent/session: {row['agent'] or '-'} / {row['session_id'] or '-'}")
            print(f"  run: {row['run_path']}")
            print(f"  next action: {row['required_next_action'] or '-'}")
        for warning in warnings:
            print(f"warning: {warning}", file=sys.stderr)
        return 0

    if action == "show":
        try:
            resolved = resolve_report_ref(args.ref, project_dir=args.project_dir)
        except FileNotFoundError as exc:
            print(str(exc))
            return 1
        metadata = resolved.get("metadata") or {}
        path = Path(resolved.get("path") or resolved.get("run_path"))
        if args.json:
            json_path = Path(metadata.get("acceptance_report_json") or path)
            if json_path.suffix.lower() != ".json":
                json_path = Path(resolved["run_path"]) / "metadata.json"
            print(json_path.read_text(encoding="utf-8", errors="replace"))
            return 0
        if args.markdown:
            md_path = Path(metadata.get("acceptance_report_md") or path)
            if md_path.suffix.lower() != ".md":
                print(f"No markdown report recorded for: {resolved['run_path']}")
                return 1
            print(md_path.read_text(encoding="utf-8", errors="replace"))
            return 0
        print(f"Run:      {resolved['run_path']}")
        print(f"Report:   {resolved['path']}")
        print(f"State:    {metadata.get('state') or metadata.get('decision') or metadata.get('status') or '-'}")
        print(f"Decision: {metadata.get('decision') or '-'}")
        print(f"Next:     {metadata.get('required_next_action') or '-'}")
        return 0

    if action == "open":
        try:
            if args.ref == "latest" and args.project_dir:
                build_report_index(args.project_dir)
            target = open_report_ref(args.ref, project_dir=args.project_dir)
        except (FileNotFoundError, OSError) as exc:
            print(str(exc))
            return 1
        print(f"Opened: {target}")
        return 0

    print("Missing reports subcommand.")
    return 1


def cmd_codex_transcript(args: argparse.Namespace) -> int:
    """Extract a Codex session into ai-gate evidence format."""
    project_dir = args.project_dir or "."
    evidence_dir = args.evidence or None
    session_path = args.session or None

    try:
        result = extract_transcript(
            project_dir=project_dir,
            session_path=session_path,
            evidence_dir=evidence_dir,
        )
    except FileNotFoundError as exc:
        print(f"Error: {exc}")
        return 1
    except Exception as exc:
        print(f"Error extracting transcript: {exc}")
        return 1

    print(f"Evidence extracted from Codex session:")
    print(f"  Session:     {result['session']}")
    print(f"  Commands:    {result['command_count']}")
    print(f"  Confidence:  {result['confidence']}")
    print(f"  Task:        {result['task'][:100]}")
    print()
    print("Generated files:")
    for key in ["transcript", "brief", "closeout", "changed_files", "commands", "plan", "audit_guard", "risks", "git_before", "git_after"]:
        path = result.get(key)
        if path:
            print(f"  {path}")
    state = load_state()
    project_key, record = find_project_for_cwd(project_dir, state)
    if project_key and record and "codex" in record.get("agents", []):
        record.update({
            "latest_decision": "FALLBACK_ONLY",
            "latest_run_path": result.get("evidence_dir", evidence_dir or os.path.join(project_dir, "evidence")),
            "latest_agent": "codex",
            "latest_session_id": Path(result["session"]).stem,
            "latest_next_action": "Fallback transcript evidence only; run ai-gate setup codex and trust hooks for full Codex supervision.",
            "latest_updated_at": utc_now(),
            "latest_late_snapshot": True,
            "latest_finalize_reason": "codex-transcript-fallback",
            "codex_fallback_observed": True,
        })
        state["projects"][project_key] = record
        save_state(state)
        print()
        print("Daemon status updated: Codex coverage is FALLBACK_ONLY for this project.")
    print()
    print("Next: ai-gate accept --repo . --evidence ./evidence --transcript ./evidence/transcript.jsonl")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="ai-gate",
        description="prove-it-ai-gate: Evidence-first AI acceptance gate",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("init", help="Initialize a prove-it-ai-gate project")

    p_transcript = subparsers.add_parser("transcript-check", help="Check transcript for truncation")
    p_transcript.add_argument("--transcript", required=True, help="Path to transcript JSONL file")

    p_workspace = subparsers.add_parser("workspace-check", help="Check workspace hygiene")
    p_workspace.add_argument("--repo", required=True, help="Path to target repository")
    p_workspace.add_argument("--task-type", help="Task type for policy resolution")
    p_workspace.add_argument("--evidence", help="Path to evidence folder (for git status files)")
    p_workspace.add_argument("--scratch-dir", help="Allowed scratch directory for temporary files")

    p_evidence = subparsers.add_parser("evidence-check", help="Check evidence folder completeness")
    p_evidence.add_argument("--evidence", required=True, help="Path to evidence folder")
    p_evidence.add_argument("--task-type", help="Task type for policy resolution")

    p_accept = subparsers.add_parser("accept", help="Run full acceptance gate")
    p_accept.add_argument("--repo", required=True, help="Path to target repository")
    p_accept.add_argument("--evidence", required=True, help="Path to evidence folder")
    p_accept.add_argument("--transcript", required=True, help="Path to transcript JSONL file")
    p_accept.add_argument("--policy", help="Policy pack name or path")
    p_accept.add_argument("--policy-dir", help="Directory containing policy YAML files")
    p_accept.add_argument("--task-type", help="Task type for policy resolution")
    p_accept.add_argument("--output", help="Output directory for reports")
    p_accept.add_argument("--scratch-dir", help="Allowed scratch directory for temporary files")

    p_capture = subparsers.add_parser("capture", help="Capture reusable learning from acceptance report")
    p_capture.add_argument("--repo", help="Path to target repository")
    p_capture.add_argument("--evidence", help="Path to evidence folder")
    p_capture.add_argument("--transcript", help="Path to transcript JSONL file")
    p_capture.add_argument("--report-json", help="Path to existing acceptance report JSON (skips re-running gate)")
    p_capture.add_argument("--policy", help="Policy pack name")
    p_capture.add_argument("--policy-dir", help="Directory containing policy YAML files")
    p_capture.add_argument("--task-type", help="Task type")
    p_capture.add_argument("--output", help="Output directory for capture")
    p_capture.add_argument("--upload", action="store_true", help="Upload capture to wiki API")
    p_capture.add_argument("--wiki-url", help="Wiki API URL")
    p_capture.add_argument("--wiki-token-id", help="Wiki API token ID")
    p_capture.add_argument("--wiki-token-secret", help="Wiki API token secret")

    p_cleanup = subparsers.add_parser("cleanup", help="Clean up old evidence folders")
    p_cleanup.add_argument("--evidence-root", required=True, help="Root directory containing evidence folders")
    p_cleanup.add_argument("--older-than", default="30d", help="Remove evidence older than N days (e.g. 30d, 7d, 1w)")
    p_cleanup.add_argument("--clean-all", action="store_true", help="Remove all old evidence including failures")
    p_cleanup.add_argument("--apply", action="store_true", help="Execute cleanup (default is dry-run)")

    p_archive = subparsers.add_parser("archive", help="Archive an evidence folder")
    p_archive.add_argument("--evidence", required=True, help="Path to evidence folder to archive")
    p_archive.add_argument("--output", help="Output directory for archive")
    p_archive.add_argument("--name", help="Custom name for the archive")

    p_ev_summary = subparsers.add_parser("evidence-summary", help="Summary of evidence folder storage")
    p_ev_summary.add_argument("--evidence-root", required=True, help="Root directory containing evidence folders")

    p_reuse = subparsers.add_parser("reuse-scan", help="Scan reuse wiki for matching assets")
    p_reuse.add_argument("--brief", required=True, help="Path to project brief markdown file")
    p_reuse.add_argument("--wiki-url", help="Wiki API URL (or set AI_GATE_WIKI_URL env)")
    p_reuse.add_argument("--wiki-token-id", help="Wiki API token ID (or set AI_GATE_WIKI_TOKEN_ID env)")
    p_reuse.add_argument("--wiki-token-secret", help="Wiki API token secret (or set AI_GATE_WIKI_TOKEN_SECRET env)")
    p_reuse.add_argument("--local-wiki", help="Path to local wiki export directory (offline mode)")
    p_reuse.add_argument("--max-results", type=int, default=30, help="Max results to return")
    p_reuse.add_argument("--output", help="Output directory for reuse scan report")

    p_watch = subparsers.add_parser("opencode-watch", help="Live desktop watcher — monitor OpenCode SQLite DB for violations")
    p_watch.add_argument("--project-dir", default=".", help="Path to target project directory")
    p_watch.add_argument("--task-type", default="audit", help="Task type for policy resolution")
    p_watch.add_argument("--interval", type=float, default=2.0, help="Poll interval in seconds")
    p_watch.add_argument("--auto-terminate", action="store_true", help="Auto-terminate OpenCode on high-severity violations")

    p_daemon = subparsers.add_parser("daemon", help="Automated evidence supervisor daemon")
    p_daemon_subs = p_daemon.add_subparsers(dest="daemon_command")

    p_daemon_register = p_daemon_subs.add_parser("register", help="Register a project for daemon supervision")
    p_daemon_register.add_argument("--project-dir", required=True, help="Path to target project directory")
    p_daemon_register.add_argument("--task-type", default="audit", help="Task type for policy resolution")
    p_daemon_register.add_argument("--evidence-root", help="Evidence root (default: <project>/evidence)")
    p_daemon_register.add_argument("--policy", help="Policy pack name or path")

    p_daemon_unregister = p_daemon_subs.add_parser("unregister", help="Unregister a supervised project")
    p_daemon_unregister.add_argument("--project-dir", required=True, help="Path to target project directory")

    p_daemon_subs.add_parser("list", help="List registered projects")

    p_daemon_start = p_daemon_subs.add_parser("start", help="Start the supervisor daemon")
    p_daemon_start.add_argument("--foreground", action="store_true", help="Run in the current terminal")
    p_daemon_start.add_argument("--interval", type=float, default=2.0, help="Poll interval in seconds")
    p_daemon_start.add_argument("--idle-seconds", type=float, default=300.0, help="Finalize idle sessions after N seconds")
    p_daemon_start.add_argument("--validation-timeout", type=int, default=120, help="Validation command timeout in seconds")
    p_daemon_start.add_argument("--policy-dir", help="Directory containing policy YAML files")
    p_daemon_start.add_argument("--no-recover", action="store_true", help="Do not finalize partially captured runs at startup")

    p_daemon_subs.add_parser("stop", help="Request daemon stop")
    p_daemon_status = p_daemon_subs.add_parser("status", help="Show daemon status")
    p_daemon_status.add_argument("--json", action="store_true", help="Print normalized status as JSON")

    p_daemon_logs = p_daemon_subs.add_parser("logs", help="Show daemon logs")
    p_daemon_logs.add_argument("--tail", type=int, default=50, help="Number of latest log lines/events to show")
    p_daemon_logs.add_argument("--json", action="store_true", help="Read JSONL log entries")

    p_install_startup = p_daemon_subs.add_parser("install-startup", help="Install Windows login startup command")
    p_install_startup.add_argument("--force", action="store_true", help="Replace an existing stale startup command")
    p_install_startup.add_argument("--dry-run", action="store_true", help="Print target path and command without writing")
    p_startup_status = p_daemon_subs.add_parser("startup-status", help="Show Windows startup command status")
    p_startup_status.add_argument("--json", action="store_true", help="Print startup status as JSON")
    p_daemon_subs.add_parser("uninstall-startup", help="Remove Windows login startup command")

    p_setup = subparsers.add_parser("setup", help="Install lifecycle hooks and integrations")
    p_setup_subs = p_setup.add_subparsers(dest="setup_command")
    p_setup_codex = p_setup_subs.add_parser("codex", help="Install Codex lifecycle hooks for pre-tool-use guarding")
    p_setup_codex.add_argument("--project-dir", default=".", help="Path to target project directory")
    p_setup_codex.add_argument("--task-type", default="audit", help="Task type for policy resolution (e.g. audit, code_change)")
    p_setup_codex.add_argument("--quiet", action="store_true", help="Suppress test hook command output")

    p_setup_daemon = p_setup_subs.add_parser("daemon", help="Register a project and optionally install daemon startup")
    p_setup_daemon.add_argument("--project-dir", required=True, help="Path to target project directory")
    p_setup_daemon.add_argument("--task-type", default="audit", help="Task type for policy resolution")
    p_setup_daemon.add_argument("--evidence-root", help="Evidence root (default: <project>/evidence)")
    p_setup_daemon.add_argument("--policy", help="Policy pack name or path")
    p_setup_daemon.add_argument("--install-startup", action="store_true", help="Install Windows login startup command")
    p_setup_daemon.add_argument("--force", action="store_true", help="Replace stale startup command when installing")

    p_reports = subparsers.add_parser("reports", help="Browse ai-gate acceptance reports")
    p_reports_subs = p_reports.add_subparsers(dest="reports_command")
    p_reports_list = p_reports_subs.add_parser("list", help="List report runs")
    p_reports_list.add_argument("--project-dir", help="Limit to one project directory")
    p_reports_list.add_argument("--status", help="Limit to a state/decision")
    p_reports_list.add_argument("--limit", type=int, default=50, help="Maximum reports to show")
    p_reports_list.add_argument("--json", action="store_true", help="Print report list as JSON")

    p_reports_show = p_reports_subs.add_parser("show", help="Show a report")
    p_reports_show.add_argument("ref", help="latest, run id, run path, or report path")
    p_reports_show.add_argument("--project-dir", help="Project directory for latest/run-id resolution")
    p_reports_show.add_argument("--json", action="store_true", help="Print JSON report")
    p_reports_show.add_argument("--markdown", action="store_true", help="Print markdown report")

    p_reports_open = p_reports_subs.add_parser("open", help="Open a report or run folder")
    p_reports_open.add_argument("ref", help="latest, run id, run path, or report path")
    p_reports_open.add_argument("--project-dir", help="Project directory for latest/run-id resolution")

    p_codex_tx = subparsers.add_parser("codex-transcript", help="Extract Codex session JSONL into ai-gate evidence folder")
    p_codex_tx.add_argument("--project-dir", default=".", help="Path to target project directory")
    p_codex_tx.add_argument("--evidence", help="Evidence output directory (default: ./evidence)")
    p_codex_tx.add_argument("--session", help="Path to specific session JSONL file (default: auto-detect latest)")

    args = parser.parse_args()

    if args.command == "init":
        return cmd_init(args)
    elif args.command == "transcript-check":
        return cmd_transcript_check(args)
    elif args.command == "workspace-check":
        return cmd_workspace_check(args)
    elif args.command == "evidence-check":
        return cmd_evidence_check(args)
    elif args.command == "accept":
        return cmd_accept(args)
    elif args.command == "capture":
        return cmd_capture(args)
    elif args.command == "cleanup":
        return cmd_cleanup(args)
    elif args.command == "archive":
        return cmd_archive(args)
    elif args.command == "evidence-summary":
        return cmd_evidence_summary(args)
    elif args.command == "reuse-scan":
        if getattr(args, "local_wiki", None):
            return cmd_reuse_scan_local(args)
        return cmd_reuse_scan(args)
    elif args.command == "opencode-watch":
        return cmd_opencode_watch(args)
    elif args.command == "daemon":
        return cmd_daemon(args)
    elif args.command == "reports":
        return cmd_reports(args)
    elif args.command == "codex-transcript":
        return cmd_codex_transcript(args)
    elif args.command == "setup":
        if getattr(args, "setup_command", None) == "codex":
            return cmd_setup_codex(args)
        if getattr(args, "setup_command", None) == "daemon":
            return cmd_setup_daemon(args)
        p_setup.print_help()
        return 0
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
