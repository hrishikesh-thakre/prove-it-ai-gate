# Failure Examples

Two minimal examples of agent failures the gate catches. Each includes a transcript and evidence folder that reproduces the failure.

## 1. Truncated Output Used for Conclusion

The agent received truncated grep output (`showing first 50 of 500`) but claimed complete coverage.

```bash
ai-gate accept \
  --repo examples/fake_project \
  --evidence examples/failures/truncated_output/evidence \
  --transcript examples/failures/truncated_output/transcript.jsonl \
  --task-type audit
```

Expected: **BLOCKED** — `transcript_truncation_check` fires.

## 2. Read-Only Audit Polluted Target Repo

The agent created helper scripts inside the target repo during a read-only audit.

```bash
ai-gate accept \
  --repo examples/fake_project \
  --evidence examples/failures/repo_pollution/evidence \
  --transcript examples/failures/repo_pollution/transcript.jsonl \
  --task-type audit
```

Expected: **REJECT** — `workspace_hygiene_check` fires.
