<div align="center">

# singit

**x402 payment and a client-side spending allowance for SingIt Ask, in a few lines of Python.**

[![PyPI](https://img.shields.io/pypi/v/singit.svg?style=flat-square&color=3775a9)](https://pypi.org/project/singit/)
[![CI](https://img.shields.io/github/actions/workflow/status/SingItAi/singit-python/ci.yml?branch=main&style=flat-square&label=CI)](https://github.com/SingItAi/singit-python/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/Python-%E2%89%A53.10-3776ab?style=flat-square&logo=python&logoColor=white)](pyproject.toml)
[![x402](https://img.shields.io/badge/x402-USDC-purple?style=flat-square)](https://x402.org)
[![Base](https://img.shields.io/badge/Base-USDC-0052FF?style=flat-square)](https://base.org)
[![Solana](https://img.shields.io/badge/Solana-USDC-9945FF?style=flat-square)](https://solana.com)

[Docs](https://singitai.app/developers/) · [Quickstart](https://singitai.app/docs/quickstart/) · [TypeScript SDK](https://github.com/SingItAi/singit-js) · [MCP server](https://github.com/SingItAi/singit-mcp) · [Examples](https://github.com/SingItAi/singit-examples)

</div>

x402 payment and client-side spending allowance for [SingIt Ask](https://github.com/SingItAi/SingItAI/tree/main/singit-ask), in a few
lines. Limits are checked in your process before signing. The separate AgentAllowance
contract covers only the SingIt app's own agent; it does not protect other agents.

## Install

```bash
pip install singit          # Base / Base Sepolia (EVM): upto + exact
pip install singit[svm]      # adds Solana: exact only (see "What isn't supported yet")
```

## Base

```python
import os
from singit import SingIt

singit = SingIt(
    evm_private_key=os.environ["AGENT_PRIVATE_KEY"],  # or evm_account=an eth_account LocalAccount
    network="base",
    max_per_call_usd=0.01,
    max_per_day_usd=1,
)

answer = singit.chat(messages=[{"role": "user", "content": "Hello"}])
print(answer["choices"][0]["message"]["content"], answer["payment"])
```

## Solana (exact only)

```python
import os
from singit import SingIt

singit = SingIt(
    solana_signer=solana_signer,  # an x402[svm] ClientSvmSigner; requires `pip install singit[svm]`
    network="solana",
    max_per_call_usd=0.01,
    max_per_day_usd=1,
)

answer = singit.chat(messages=[{"role": "user", "content": "Hello"}], scheme="exact")
```

## Base Sepolia (testnet)

SingIt has no testnet deployment, so there is no pinned `pay_to` to default to: pass one
explicitly (e.g. your own test merchant, or a facilitator's own test receiver).

```python
singit = SingIt(
    evm_private_key=os.environ["AGENT_PRIVATE_KEY"], network="base-sepolia",
    pay_to=os.environ["TEST_MERCHANT_PAY_TO"],  # required: no SingIt deployment to pin a default against
    max_per_call_usd=0.01, max_per_day_usd=1,
)
```

## Guards

Every call pins the merchant's offer before signing anything, the same way
`singit-ask/src/metered-buyer.mjs` and `solana-buyer.mjs` in the [main repository](https://github.com/SingItAi/SingItAI/tree/main/singit-ask/src) do for the service's own buyers:

- **Network/asset/payTo.** The offer's `network`, `asset` and `payTo` must exactly match this
  `SingIt` instance's configured network (the built-in pin for `base`/`solana`, or the `pay_to`
  you pass for `base-sepolia`). A merchant offering a different asset or recipient is refused
  before anything is signed.
- **Per-call ceiling (`max_per_call_usd`).** The offer's atomic amount must be at or below
  `max_per_call_usd` converted to micro-USDC. A costlier offer is refused.
- **Per-day ceiling (`max_per_day_usd`).** An in-memory counter per client instance and per UTC day.
  Reserved at the offer's worst-case ceiling before signing, settled down to the actual billed
  amount for `upto` only. `exact` always counts the full offer amount. **Not durable**: it resets on restart and is not shared across processes —
  it is a client-side allowance guard, not a ledger. For the SingIt app agent’s separate on-chain limit, see
  `allowance()` below.
- **Settlement receipt.** The `payment-response` header's network and payer are checked against
  the request; a mismatch is treated as unresolved rather than returned as a success.

This SDK does **not** wait for on-chain confirmation of the settlement transaction;
`payment["txHash"]` is returned as reported by the merchant's `payment-response` header for the
caller to check independently if needed.

## API

- `SingIt(...)` — `evm_private_key | evm_account` (EVM; `evm_account` is any `eth_account`
  `LocalAccount`), `solana_signer` (an `x402[svm]` signer, for `network="solana"`), `network`,
  `base_url` (default `https://ask.singitai.app`), `app_base_url` (default
  `https://app.singitai.app`), `pay_to`, `rpc_url`, `max_per_call_usd`, `max_per_day_usd` (both
  required).
- `chat(model=None, messages=..., max_tokens=None, scheme="upto", route="completions")` —
  OpenAI-shaped chat completion. `scheme="upto"` (default) pays only the actual answer cost, up
  to the offered ceiling, on EVM networks; `"exact"` pays that ceiling flat and is the only
  scheme supported on Solana (see below). `route="completions"` (default) calls
  `POST /v1/chat/completions`, live today. `route="per-model"` calls `POST /v1/chat/{model}`
 , live; `model` is then required.
- `wallet_report(address, days=None, api_key=None)` — `GET {app_base_url}/v1/wallet/{address}/reconciliation`
 . The report is private: `api_key` must be a SingIt API key of the account that signs in
  with `address`; without one the server answers 401, and the SDK says so.
- `pricing()` — `GET {base_url}/pricing.json`, live.
- `allowance(address, **kwargs)` — `wallet_report(address, **kwargs)["allowance"]`: the on-chain
  spending limit set in the SingIt app, with what has been spent/remains today.

## What isn't supported yet

- **Solana `upto` is not supported.** The official `x402` Python package (2.25.0) ships an EVM
  `upto` client (`x402.mechanisms.evm.upto.client.UptoEvmScheme`) but has no Solana equivalent —
  only `x402.mechanisms.svm.exact.client.ExactSvmScheme`. This corrects an earlier assumption
  that Python might lack EVM `upto` too: it does not, Base/Base Sepolia support both schemes.
  Solana support here is `exact` only, and calling `chat(scheme="upto", network="solana")` raises
  clearly rather than silently falling back.
- No on-chain confirmation of the settlement transaction.
- The Solana `exact` client in the official package resolves a blockhash over a live RPC call
  during payload construction, so it cannot be exercised fully offline; this SDK's own test suite
  therefore only covers Solana at the offer-selection/guard level (see `tests/test_solana_offer.py`).
- `base-sepolia` is useful for integration-testing against a merchant you control; SingIt itself
  has no testnet deployment to pay.

Framework fetch adapters are currently available only in the TypeScript SDK.

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md). Security problems: email contact@singitai.app, not a public
issue ([SECURITY.md](SECURITY.md)). MIT licensed. The TypeScript equivalent is
[`@singitai/sdk`](https://github.com/SingItAi/singit-js).
