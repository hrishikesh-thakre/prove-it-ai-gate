# Contributing

`prove-it-ai-gate` is early beta. Contributions that improve the tool's ability to catch real AI agent failures are welcome.

## Ways to Contribute

### Report a False Accept

A false accept is when the gate returns `ACCEPT` or `ACCEPT_WITH_CONDITIONS` for AI-generated work that should have been `REJECT` or `BLOCKED`. Use the **False Accept Report** issue template.

Include:
- A minimal transcript / evidence folder that reproduces the issue
- The expected decision
- The actual decision
- Why the work should not have been accepted

### Report a False Reject

A false reject is when the gate returns `REJECT` or `BLOCKED` for work that should have been `ACCEPT`. Use the **False Reject Report** issue template.

### Suggest a New Check

If you have observed a failure mode the gate does not catch, open a feature request describing:
- The failure pattern
- What evidence would prove it happened
- Example transcript / evidence if available

### Improve Documentation

Docs are in `docs/`. PRs fixing typos, clarifying explanations, or adding examples are welcome.

## Development Setup

```bash
git clone https://github.com/hrishikesh-thakre/prove-it-ai-gate.git
cd prove-it-ai-gate
pip install -e .
pip install pytest build twine
```

## Running Tests

```bash
python -m pytest tests/ -v
python scripts/run_dogfood.py
```

## Build

```bash
python -m build
python -m twine check dist/*
```

## Conventions

- Keep the public repo generic. No private project names, local paths, or real transcripts.
- Use fake/sample fixtures only.
- Maintain 0 false accepts on the dogfood suite.
- False rejects should be justified (the gate should not be too aggressive).
- Experimental features go behind `--experimental` flags or are clearly documented as unstable.
