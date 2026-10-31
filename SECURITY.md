# Security policy

SingIt handles payments in USDC, so we take security reports seriously and appreciate responsible
disclosure.

## Reporting a vulnerability

**Please do not open a public issue or pull request for a security problem.**

Email **contact@singitai.app** with:

- what the problem is and which part of `singit-python` it affects,
- steps to reproduce it, or a proof of concept,
- what an attacker could do with it.

Do not include real private keys, seed phrases or other people's data in the report. If you need to
send something sensitive, say so in a first message and we will agree on a way to share it.

We will confirm that we received your report and keep you updated until it is resolved. Please give us
a reasonable time to fix the problem before you disclose it publicly.

## Scope

- The code in this repository.
- Ways to make a client built on it spend more than the limits it was given, pay an address or price
  it did not agree to, or repeat a payment.
- The wider SingIt services and contracts are covered by the
  [main repository's policy](https://github.com/SingItAi/SingItAI/blob/main/SECURITY.md).

Out of scope: findings in third-party services we depend on (report those to the vendor), denial of
service through traffic volume, and reports that need a compromised device or wallet.

Please test only against your own accounts and funds.

## Supported versions

Only the latest release and `main` are supported.
