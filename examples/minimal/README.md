# Minimal Working Example

This is the smallest possible setup to try `prove-it-ai-gate` end-to-end.

## Usage

```bash
# Install
pip install prove-it-ai-gate

# Run the gate against this example
ai-gate accept \
  --repo examples/fake_project \
  --evidence examples/minimal/evidence \
  --transcript examples/minimal/transcript.jsonl \
  --task-type audit
```

Expected output: `Decision: ACCEPT`

## What's included

- `transcript.jsonl` — A minimal agent transcript with clean tool calls
- `evidence/` — A complete evidence folder following the contract
- The `--repo` points to `examples/fake_project/` which is a clean dummy repo

## Next steps

1. Copy this folder as a starting point for your own agent tasks
2. Modify `brief.md` to describe your actual task
3. Replace `transcript.jsonl` with your agent's actual transcript
4. Run `ai-gate accept` to check your agent's work
