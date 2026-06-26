# Confidence Rules

The confidence claim check enforces discipline on agent confidence statements.

## Default Thresholds

| Threshold | Value | Rule |
|---|---|---|
| High confidence | 0.90 | Above this, deterministic evidence artifacts are required |
| Heuristic cap | 0.75 | Confidence from heuristic extraction methods cannot exceed this |
| Absolute claims | 1.0 / 100% | Requires scoped deterministic evidence |

## What It Checks

1. **Confidence value extraction**: Scans closeout.md, risks.md, plan.md, brief.md, and transcript for confidence claims like `Confidence: 0.95` or `confidence is 0.85`
2. **Evidence requirement**: If confidence exceeds the high threshold, verifies that `artifacts/inventory.json` and `artifacts/extracted_findings.json` exist
3. **Heuristic extraction cap**: If the transcript shows heuristic methods (grep, filename matching, regex), caps confidence to 0.75
4. **Completeness claims**: Flags bare "Complete" or "100%" claims that lack scoped corpus definitions

## Example: Good (passes)

```
Confidence: 0.65
Data completeness: Partial for src/ directory (45 of 200 files sampled)
Extraction method: keyword matching with false-negative warning
```

## Example: Bad (rejected)

```
Confidence: 0.95
Data completeness: Complete
Extraction method: grep for filenames
```

## Configuring Thresholds

Thresholds are configurable per policy pack:

```yaml
confidence_thresholds:
  high: 0.90
  heuristic_cap: 0.75
```

Tighter policies (e.g., corpus audits) may use lower thresholds.
