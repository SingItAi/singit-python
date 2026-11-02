# Changelog

This repository was split from [SingItAi/SingItAI](https://github.com/SingItAi/SingItAI)
(`sdk/python`) with its history kept.

## Unreleased

- Persist daily spend by default in a local JSONL log, scoped by wallet and network,
  shared across processes on one host, with reservations before signing and safe
  accounting for unresolved payments. `spend_log=False` retains in-memory behavior.
- Add offline `singit spend summary` (text/JSON) and `spend export --csv`, plus
  `read_spend` and `spend_summary` helpers.
- This is a local client-side guard, not on-chain enforcement, a ledger, proof of
  payment or tamper-proof accounting. Editing/deleting the file, other machines,
  or other tools using the wallet bypass it. Network filesystems are unsupported.

## 0.1.1 - 2026-10-31

- Repository moved to SingItAi; links updated.
- README header with badges and links; links to the main repository now work outside the monorepo.
- Project URLs in `pyproject.toml` point at this repository. They reach the PyPI page with this
  version.
- CI (Python 3.10, 3.12 and 3.13), build and `twine check`, issue and pull request templates,
  security policy, Dependabot.

## 0.1.0

First published version of `singit` (see the history before the split).
