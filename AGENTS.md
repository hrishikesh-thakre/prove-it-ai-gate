# prove-it-ai-gate

This project builds and uses `prove-it-ai-gate`, a local CLI acceptance gate for AI-generated engineering work.

## Build and Test

```bash
pip install -e .
python -m pytest tests/ -q
python scripts/run_dogfood.py
```

## Project Structure

- `prove_it_ai_gate/` — Core package (CLI, gate engine, checkers)
- `tests/` — 80 tests across 10 test modules
- `examples/` — Sample evidence, dogfood cases, failure examples
- `docs/` — Documentation
- `scripts/` — gate.sh, gate.ps1, run_dogfood.py

## Conventions

- Python 3.9+, pyyaml only dependency
- `pytest` for testing; no unittest
- All imports use `prove_it_ai_gate.xxx` (package renamed from `src`)
- Keep public repo generic — no real project names, paths, or transcripts

## Evidence discipline

After completing any task in this repo, run the gate using bash:

```bash
python -m prove_it_ai_gate.cli accept --repo . --evidence ./evidence --transcript transcript.jsonl --task-type audit
```

For code changes:
```bash
python -m prove_it_ai_gate.cli accept --repo . --evidence ./evidence --transcript transcript.jsonl --task-type code_change
```
