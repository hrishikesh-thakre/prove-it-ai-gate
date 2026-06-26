from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path

from .types import CheckResult, Issue, Severity

CANONICAL_BOOKS = {
    "Reusable Assets",
    "Architecture Decisions",
    "Project Patterns",
    "Failure Modes",
    "Runbooks",
    "Project Learnings",
    "Diagram Summaries",
}

REUSE_CLASSIFICATIONS = [
    "reuse_as_is",
    "reuse_with_minor_adaptation",
    "reuse_with_major_adaptation",
    "reference_only",
    "do_not_reuse",
]


class WikiClient:
    def __init__(self, base_url: str, token_id: str, token_secret: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token_id = token_id
        self.token_secret = token_secret

    def request(self, method: str, path: str, payload: dict | None = None, timeout: int = 30) -> dict:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            method=method,
            headers={
                "Authorization": f"Token {self.token_id}:{self.token_secret}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    raw = response.read().decode("utf-8")
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if exc.code == 429 and attempt < 2:
                    time.sleep(5 + (attempt * 5))
                    continue
                raise RuntimeError(f"{method} {path} HTTP {exc.code}: {detail[:300]}") from exc
            except urllib.error.URLError as exc:
                if attempt < 2:
                    time.sleep(3)
                    continue
                raise RuntimeError(f"{method} {path} failed: {exc.reason}") from exc
        raise RuntimeError(f"{method} {path} failed after retries")

    def list_all(self, endpoint: str) -> list[dict]:
        items: list[dict] = []
        offset = 0
        count = 500
        while True:
            result = self.request("GET", f"/api/{endpoint}?count={count}&offset={offset}")
            batch = result.get("data", [])
            items.extend(batch)
            if len(batch) < count:
                return items
            offset += count


def _slug(value: str) -> str:
    value = value.lower().replace("&", "and").replace("`", "")
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-")


def _word_set(value: str) -> set[str]:
    stop = {"the", "a", "an", "and", "or", "for", "with", "via", "as", "of", "to", "in", "is", "on", "at", "by"}
    return {word for word in re.findall(r"[a-z0-9]+", value.lower()) if word not in stop and len(word) > 1}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _parse_front_matter(markdown: str) -> dict[str, str]:
    if not markdown.startswith("---"):
        return {}
    lines = markdown.splitlines()
    end = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = index
            break
    if end is None:
        return {}
    data: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" not in line or line.startswith(" "):
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip()
    return data


def _clean_list(value) -> list[str]:
    if isinstance(value, list):
        return value
    if not isinstance(value, str):
        return []
    value = value.strip()
    if not value or value == "[]":
        return []
    if value.startswith("[") and value.endswith("]"):
        return [part.strip().strip("'\"") for part in value[1:-1].split(",") if part.strip()]
    return [value.strip().strip("'\"")]


def _score_match(brief_words: set[str], page: dict) -> float:
    title = page.get("name", "")
    title_words = _word_set(title)
    tags = _clean_list(page.get("tags", ""))
    tag_words: set[str] = set()
    for tag in tags:
        tag_words.update(_word_set(tag))

    all_page_words = title_words | tag_words
    jaccard_score = _jaccard(brief_words, all_page_words)

    title_lower = title.lower()

    bonus = 0.0
    for word in brief_words:
        if word in title_lower:
            bonus += 0.05

    maturity = page.get("maturity", "")
    if maturity in ("production_proven", "reusable"):
        bonus += 0.10
    elif maturity == "validated":
        bonus += 0.05
    elif maturity in ("deprecated", "do_not_reuse"):
        bonus -= 0.20

    reuse_status = page.get("reuse_status", "")
    if reuse_status == "reuse_as_is":
        bonus += 0.10
    elif reuse_status == "do_not_reuse":
        bonus -= 0.20

    return min(1.0, jaccard_score + bonus)


def _classify_reuse(page: dict) -> str:
    maturity = page.get("maturity", "")
    reuse_status = page.get("reuse_status", "")

    if reuse_status == "do_not_reuse" or maturity == "do_not_reuse":
        return "do_not_reuse"
    if reuse_status == "reuse_as_is" and maturity in ("production_proven", "reusable"):
        return "reuse_as_is"
    if reuse_status in ("reuse_with_changes", "reuse_as_is"):
        return "reuse_with_minor_adaptation"
    if reuse_status == "reference_only":
        return "reference_only"
    if maturity in ("experimental", "deprecated"):
        return "reference_only"
    return "reference_only"


def scan_reuse_wiki(
    brief_path: str,
    wiki_url: str,
    wiki_token_id: str,
    wiki_token_secret: str,
    max_results: int = 30,
) -> dict:
    brief_text = Path(brief_path).read_text(encoding="utf-8", errors="replace")
    brief_words = _word_set(brief_text)

    client = WikiClient(wiki_url, wiki_token_id, wiki_token_secret)

    books = client.list_all("books")
    book_by_id = {b["id"]: b.get("name", "") for b in books}

    canonical_book_ids = {b["id"] for b in books if b.get("name") in CANONICAL_BOOKS}

    pages = client.list_all("pages")
    canonical_pages = [p for p in pages if p.get("book_id") in canonical_book_ids]

    scored_pages: list[dict] = []
    fetched_count = 0

    fetch_limit = min(len(canonical_pages), max_results * 5)

    for page in canonical_pages[:fetch_limit]:
        try:
            detail = client.request("GET", f"/api/pages/{page['id']}", timeout=20)
            markdown = detail.get("markdown", "") or ""
            fm = _parse_front_matter(markdown)
            body = markdown.split("---", 2)[-1] if markdown.count("---") >= 2 else markdown

            body_words = _word_set(body)

            body_overlap = _jaccard(brief_words, body_words)

            enriched = {
                "id": page["id"],
                "name": page.get("name", ""),
                "book": book_by_id.get(page.get("book_id"), ""),
                "type": fm.get("type", ""),
                "status": fm.get("status", ""),
                "maturity": fm.get("maturity", ""),
                "reuse_status": fm.get("reuse_status", ""),
                "owner": fm.get("owner", ""),
                "tags": _clean_list(fm.get("tags", "")),
                "related_projects": _clean_list(fm.get("related_projects", "")),
                "last_validated": fm.get("last_validated", ""),
                "relevance_score": round(_score_match(brief_words, {
                    "name": page.get("name", ""),
                    "maturity": fm.get("maturity", ""),
                    "reuse_status": fm.get("reuse_status", ""),
                    "tags": fm.get("tags", ""),
                }), 3),
                "body_overlap": round(body_overlap, 3),
                "reuse_classification": _classify_reuse({
                    "maturity": fm.get("maturity", ""),
                    "reuse_status": fm.get("reuse_status", ""),
                }),
                "needs_human_review": fm.get("reuse_status") == "needs_human_review" or fm.get("status") == "draft",
            }
            scored_pages.append(enriched)
            fetched_count += 1
        except Exception:
            continue

    scored_pages.sort(key=lambda p: (-p["relevance_score"], p["name"]))
    top_pages = scored_pages[:max_results]

    type_counts = dict(Counter(p["type"] for p in top_pages))
    book_counts = dict(Counter(p["book"] for p in top_pages))
    classification_counts = dict(Counter(p["reuse_classification"] for p in top_pages))

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "brief_path": brief_path,
        "brief_summary": brief_text[:200].replace("\n", " "),
        "wiki_url": wiki_url,
        "totals": {
            "canonical_pages_available": len(canonical_pages),
            "pages_fetched": fetched_count,
            "results_returned": len(top_pages),
        },
        "counts": {
            "by_type": type_counts,
            "by_book": book_counts,
            "by_reuse_classification": classification_counts,
        },
        "results": top_pages,
    }


def format_reuse_report(scan_result: dict) -> str:
    lines = [
        "# Reuse Scan Report",
        "",
        f"Generated: {scan_result['generated_at']}",
        f"Brief: {scan_result['brief_summary']}",
        "",
        "## Summary",
        "",
        f"- Canonical pages available: {scan_result['totals']['canonical_pages_available']}",
        f"- Fetched and scored: {scan_result['totals']['pages_fetched']}",
        f"- Top results shown: {scan_result['totals']['results_returned']}",
        "",
        "## By Type",
        "",
    ]
    for key, value in sorted(scan_result["counts"]["by_type"].items()):
        lines.append(f"- `{key}`: {value}")
    lines.append("")
    lines.append("## By Reuse Classification")
    lines.append("")
    for cls in REUSE_CLASSIFICATIONS:
        count = scan_result["counts"]["by_reuse_classification"].get(cls, 0)
        if count:
            lines.append(f"- `{cls}`: {count}")
    lines.append("")

    if scan_result["results"]:
        lines.append("## Top Matches")
        lines.append("")
        lines.append("| # | Asset | Type | Maturity | Reuse | Score | Classification |")
        lines.append("|---|---|---|---|---|---|---|")
        for i, page in enumerate(scan_result["results"], 1):
            maturity = page.get("maturity", "")
            if page.get("needs_human_review"):
                maturity += " !REVIEW"
            reuse = page.get("reuse_status", "")
            if reuse == "do_not_reuse":
                reuse = "DO NOT REUSE"
            lines.append(
                f"| {i} | {page['name'][:60]} | `{page['type']}` | "
                f"`{maturity}` | `{page['reuse_status']}` | "
                f"{page['relevance_score']:.2f} | `{page['reuse_classification']}` |"
            )
        lines.append("")
    else:
        lines.append("_No matching assets found._")
        lines.append("")

    do_not_reuse = [p for p in scan_result["results"] if p["reuse_classification"] == "do_not_reuse"]
    if do_not_reuse:
        lines.append("## Do Not Reuse Warnings")
        lines.append("")
        for page in do_not_reuse:
            lines.append(f"- **{page['name']}** ({page['book']}): maturity=`{page['maturity']}`, reuse_status=`{page['reuse_status']}`")
        lines.append("")

    needs_review = [p for p in scan_result["results"] if p.get("needs_human_review")]
    if needs_review:
        lines.append("## Needs Human Review")
        lines.append("")
        for page in needs_review:
            lines.append(f"- **{page['name']}** ({page['book']}): type=`{page['type']}`")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("*Report generated by prove-it-ai-gate Reuse Scout.*")
    lines.append("")

    return "\n".join(lines)
