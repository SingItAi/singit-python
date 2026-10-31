# Contributing to singit (Python)

Thanks for taking a look. Bug reports, fixes and documentation are welcome.

This SDK signs real payments, so changes to the guards (`src/singit/guards.py`, `pins.py`, `client.py`)
are reviewed more strictly than the rest.

## Set up

Python 3.10 or newer.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[test,svm]"
pytest
```

Tests use local mocks for the merchant, the chain and the wallet. No funded wallet, API key or
network access is needed, and tests must never send a real payment.

## Pull requests

- Branch from `main` and keep each pull request to one change.
- Add or update tests for behaviour you change, and update `README.md` and `CHANGELOG.md`.
- The limits and pins are checked **before** anything is signed. A change must keep that order.
- A payment whose outcome is unknown is never retried automatically.
- Describe what changed, why, and how you checked it. Say what you did **not** check.
- Security problems: do not open a public issue. See [SECURITY.md](SECURITY.md).
- Do not rewrite history on a branch someone has already reviewed.

## Releases

Releases are cut by the maintainers: bump `version` in `pyproject.toml` (and the version check in
`tests/test_package.py`), add a `CHANGELOG.md` entry, tag `vX.Y.Z` and publish.
