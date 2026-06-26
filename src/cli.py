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


DEFAULT_POLICY_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "policies")


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
    from .reuse_scout import format_reuse_report, _word_set, _score_match, _classify_reuse, _parse_front_matter, _clean_list
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
            body_words = _word_set(body)
            body_overlap = 0.0
            if brief_words and body_words:
                from .reuse_scout import _jaccard
                body_overlap = _jaccard(brief_words, body_words)

            enriched = {
                **page,
                "relevance_score": round(_score_match(brief_words, page), 3),
                "body_overlap": round(body_overlap, 3),
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

    p_reuse = subparsers.add_parser("reuse-scan", help="Scan reuse wiki for matching assets")
    p_reuse.add_argument("--brief", required=True, help="Path to project brief markdown file")
    p_reuse.add_argument("--wiki-url", help="Wiki API URL (or set AI_GATE_WIKI_URL env)")
    p_reuse.add_argument("--wiki-token-id", help="Wiki API token ID (or set AI_GATE_WIKI_TOKEN_ID env)")
    p_reuse.add_argument("--wiki-token-secret", help="Wiki API token secret (or set AI_GATE_WIKI_TOKEN_SECRET env)")
    p_reuse.add_argument("--local-wiki", help="Path to local wiki export directory (offline mode)")
    p_reuse.add_argument("--max-results", type=int, default=30, help="Max results to return")
    p_reuse.add_argument("--output", help="Output directory for reuse scan report")

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
    elif args.command == "reuse-scan":
        if getattr(args, "local_wiki", None):
            return cmd_reuse_scan_local(args)
        return cmd_reuse_scan(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
