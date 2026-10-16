# Contributing

Thanks for considering a contribution!

## Development setup

```bash
git clone https://github.com/iowap-org/iowap-client-mcp
cd iowap-client-mcp
python -m venv .venv && .venv/bin/pip install -e ".[mcp,dev]"
.venv/bin/pytest
```

## Guidelines

- Python 3.11+, style: `ruff` (line length 100).
- Tests are part of every change: `pytest` must pass before submitting a PR.
- Commit messages follow conventional commits (`feat:`, `fix:`, `docs:`, `chore:`).
- Documentation lives in this repo (README) and in the
  [iowap-docs](https://github.com/iowap-org/iowap-docs) repo.
- Example values in docs, docstrings, and tests must be generic
  (e.g. `NODE23A4`-style IDs), never real deployment values.
- Public documentation is English-only.

## Pull requests

- Keep PRs focused; one feature or fix per PR.
- Describe what changed, why, and how it was verified.
- CI (when present) must be green.