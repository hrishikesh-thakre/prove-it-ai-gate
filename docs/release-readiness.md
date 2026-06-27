# Release Readiness

`prove-it-ai-gate` package v0.3.2 with experimental supervisor work through the
v0.6 hardening line.

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
| `ai-gate daemon register/list/start/stop/status` | Experimental | Local supervisor for registered projects |
| `ai-gate daemon logs` | Experimental | Redacted operational daemon logs |
| `ai-gate daemon install-startup/uninstall-startup/startup-status` | Experimental | Windows login startup only |
| `ai-gate setup daemon` | Experimental | Guided daemon registration and optional startup install |
| `ai-gate setup codex` | Experimental | Project-local Codex hook install; requires `/hooks` trust |
| `ai-gate codex-transcript` | Experimental | Fallback/reconciliation only, marks `FALLBACK_ONLY` |
| `ai-gate reports list/show/open` | Experimental | Static report browsing; no local web server |
| `ai-gate opencode-watch` | Experimental | Manual/debug OpenCode Desktop watcher |
| `ai-gate reuse-scan` | Experimental | Local-wiki mode stable; live API mode needs rate-limiting tuning |
| `ai-gate capture` | Experimental | Output format stable; wiki upload path tested but limited |
| `ai-gate cleanup` | Experimental | Dry-run safe; `--apply` requires confirmation |
| `ai-gate archive` | Experimental | Basic file copy with manifest |
| `ai-gate evidence-summary` | Experimental | Storage reporting only |

## Known Limitations

1. **Confidence check false positives**: The word "heuristic" appearing in filenames (e.g., `heuristic_checker.py`) can trigger the heuristic extraction check. Mitigated in v0.3 by scoping detection to tool output fields only, but edge cases remain.

2. **Git status dependency**: Workspace hygiene checks require `git_status_before.txt` and `git_status_after.txt` in the evidence folder. If missing, the gate falls back to live `git status` on the target repo, which may lack historical context. The gate emits a warning when this occurs.

3. **Transcript format**: The JSONL parser supports common formats, but Codex
   supervision must use official hooks as the primary source. Transcript
   reconstruction is fallback/reconciliation only and is marked
   `FALLBACK_ONLY`.

4. **Single-platform startup integration**: Windows Startup-folder integration
   is implemented. macOS/Linux service-manager integration is not implemented.

5. **Daemon dogfood still required**: OpenCode and Codex supervision are covered
   by fixtures and smoke checks, but real Desktop dogfood remains required
   before treating the daemon as more than experimental.

6. **Reuse Scout scoring**: TF-IDF scoring with Jaccard overlap works for keyword-rich briefs but may produce low relevance scores (0.20-0.40) for abstract or domain-specific briefs. Semantic/embedding-based search is not implemented.

7. **No incremental acceptance**: The gate runs all checks every time. There is no caching, partial re-run, or incremental check mode.

8. **Operational logs are not evidence logs**: Daemon logs intentionally redact
   raw stdout/stderr and transcript bodies. The evidence folder remains the
   authoritative evidence record.

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

## Supervisor Readiness

Implemented experimental supervisor capabilities:

- registered project supervision
- run-scoped evidence folders
- OpenCode SQLite supervision
- Codex hook-first supervision
- fallback transcript marking
- daemon lock/heartbeat/stale PID recovery
- safer line-level Codex spool checkpointing
- redacted daemon logs
- Windows startup install/status/uninstall
- static report listing/show/open
- normalized project state and coverage fields

Status states:

- `UNSUPERVISED`
- `FALLBACK_ONLY`
- `RUNNING`
- `BLOCKED`
- `REJECT`
- `ACCEPT_WITH_CONDITIONS`
- `ACCEPT`

Coverage states:

- `UNSUPERVISED`
- `HOOKS_CONFIGURED_UNTRUSTED_UNVERIFIED`
- `HOOKS_OBSERVED`
- `FALLBACK_ONLY`

## What NOT to Use This Tool For (Yet)

- Production gatekeeping without human review
- Security audit sign-off (the tool checks evidence, not security correctness)
- Regulatory compliance (no formal certification)
- Replacing code review on high-risk changes (auth, crypto, data migration)
- Gating PRs automatically in CI (GitHub Actions integration not yet built)
- Multi-user or team workflows (single-user local daemon only)
- Unattended production enforcement without reviewing acceptance reports

## Test Coverage

- 246 tests across the test suite as of the v0.6 hardening pass
- 8 false-accept tests (known bad patterns)
- 5 false-reject tests (known good patterns)
- 11 adversarial attack tests (evidence fabrication, inflation, laundering)
- 6 automated dogfood cases (3 bad, 3 good)
- Focused daemon/Codex suite: 145 tests
- Smoke test: `node scripts/smoke-test.mjs` reports 52 checks

## Package Structure

Package root is `prove_it_ai_gate/` with entry point `prove_it_ai_gate.cli:main`. Policy files are bundled as package data under `prove_it_ai_gate/policies/`.

## PyPI Release Process

### Local verification

```bash
python -m pip install build twine
python -m build
python -m twine check dist/*
```

### TestPyPI (recommended before production PyPI)

```bash
python -m twine upload --repository testpypi dist/*
pip install --index-url https://test.pypi.org/simple/ prove-it-ai-gate
```

### Production PyPI

Tag a release commit:

```bash
git tag v0.3.2
git push origin v0.3.2
```

The GitHub Actions `release.yml` workflow builds, checks, and publishes to PyPI via Trusted Publishing on every `v*` tag push.

### Trusted Publishing setup

1. In PyPI project settings, add a Trusted Publisher:
   - Owner: `hrishikesh-thakre`
   - Repository: `prove-it-ai-gate`
   - Workflow: `release.yml`
2. In GitHub repo settings → Environments, create a `pypi` environment.
3. Push a `v*` tag to trigger the release workflow.
