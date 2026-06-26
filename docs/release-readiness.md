# Release Readiness

`prove-it-ai-gate` v0.3.1 — early beta assessment.

## Supported Commands

### Stable

| Command | Status | Notes |
|---|---|---|
| `ai-gate init` | Stable | |
| `ai-gate transcript-check` | Stable | Tolerant JSONL parser; supports multiple agent platforms |
| `ai-gate workspace-check` | Stable | Git status parsing; read-only violation detection |
| `ai-gate evidence-check` | Stable | Evidence folder schema validation |
| `ai-gate accept` | Stable | Full acceptance gate with 12 checks |

### Experimental

| Command | Status | Notes |
|---|---|---|
| `ai-gate reuse-scan` | Experimental | Local-wiki mode stable; live API mode needs rate-limiting tuning |
| `ai-gate capture` | Experimental | Output format stable; wiki upload path tested but limited |
| `ai-gate cleanup` | Experimental | Dry-run safe; `--apply` requires confirmation |
| `ai-gate archive` | Experimental | Basic file copy with manifest |
| `ai-gate evidence-summary` | Experimental | Storage reporting only |

## Known Limitations

1. **Confidence check false positives**: The word "heuristic" appearing in filenames (e.g., `heuristic_checker.py`) can trigger the heuristic extraction check. Mitigated in v0.3 by scoping detection to tool output fields only, but edge cases remain.

2. **Git status dependency**: Workspace hygiene checks require `git_status_before.txt` and `git_status_after.txt` in the evidence folder. If missing, the gate falls back to live `git status` on the target repo, which may lack historical context. The gate emits a warning when this occurs.

3. **Transcript format**: The JSONL parser supports common formats but has not been tested against MCP-formatted transcripts, OpenCode transcripts, or other platform-specific formats. Each platform may require minor parser adjustments.

4. **Single-platform testing**: All development and dogfood testing has been on a single platform (Windows/Python 3.11). Cross-platform behavior (Linux, macOS, Python 3.9-3.12) is verified by CI but limited to the test suite.

5. **Reuse Scout scoring**: TF-IDF scoring with Jaccard overlap works for keyword-rich briefs but may produce low relevance scores (0.20-0.40) for abstract or domain-specific briefs. Semantic/embedding-based search is not implemented.

6. **No incremental acceptance**: The gate runs all checks every time. There is no caching, partial re-run, or incremental check mode.

## False Accept / False Reject Definitions

- **False accept**: The gate returns `ACCEPT` for work that should have been rejected or blocked. This is the most dangerous failure mode — it means bad agent work passed through undetected.

- **False reject**: The gate returns `REJECT` or `BLOCKED` for work that should have been accepted. This creates friction and erodes user trust.

### Current Rates (dogfood suite, 6 cases)

- False accept rate: 0% (0/6)
- False reject rate: 0% (0/6)

These rates are measured on a curated 6-case dogfood suite. Real-world rates may differ.

## Public/Private Safety Rules

### Public Repo Must NOT Include

- Real project names or customer names
- Local file paths (C:\Users\, D:\, etc.)
- Private wiki content or exports
- Real agent transcripts from internal projects
- Business strategy notes or internal decision logs
- Proprietary code, DRM logic, or publisher-specific content
- Secrets, API tokens, or environment values

### Public Repo Contains Only

- Generic source code with no project-specific references
- Fake/sample fixtures in `examples/`
- Publicly safe dogfood cases with fake data
- Policy YAML files with no internal project mappings
- Documentation describing the tool, not internal usage

## What NOT to Use This Tool For (Yet)

- Production gatekeeping without human review
- Security audit sign-off (the tool checks evidence, not security correctness)
- Regulatory compliance (no formal certification)
- Replacing code review on high-risk changes (auth, crypto, data migration)
- Gating PRs automatically in CI (GitHub Actions integration not yet built)
- Multi-user or team workflows (single-user CLI only)

## Test Coverage

- 80 tests across 10 test modules
- 8 false-accept tests (known bad patterns)
- 5 false-reject tests (known good patterns)
- 11 adversarial attack tests (evidence fabrication, inflation, laundering)
- 6 automated dogfood cases (3 bad, 3 good)

## Package Structure

Package root is `prove_it_ai_gate/` with entry point `prove_it_ai_gate.cli:main`. Policy files are bundled as package data under `prove_it_ai_gate/policies/`.
