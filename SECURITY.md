# Security Policy

Emergenzix is an early-stage quantitative trading product. Security defects can expose account data or create financial loss, so report suspected vulnerabilities privately and do not include live credentials in issues.

## Reporting a vulnerability

Until a dedicated private security contact is published, contact the repository maintainer through the private contact method available on the GitHub profile. Do not publish exploit details, credentials, or a reproducible attack against a live account in a public issue.

Please include the affected component, impact, reproduction steps using a safe environment, and any suggested mitigation. Do not test against accounts or systems you do not own or have explicit permission to assess.

## Security invariants

- No API keys, passwords, tokens, private keys, or production credentials in source control.
- Exchange credentials remain server-side and use least privilege.
- Withdrawal permissions must not be requested or required.
- DEMO, PAPER, TESTNET, and LIVE configurations remain isolated.
- LIVE trading is disabled by default and requires separate documented approval.
- Authentication, authorization, risk checks, and order validation are enforced server-side.
- Unknown mode, stale data, failed risk checks, and uncertain order state fail closed.
- Logs, screenshots, test fixtures, and issue reports must redact sensitive data.
- Dependencies must be reviewed for known vulnerabilities and license compatibility.
- Security fixes should include regression tests where feasible.

## Scope

The scope includes the Emergenzix repository and any application services that are explicitly operated as part of this project. Research infrastructure in NEMT is a separate system and must be reported through its own appropriate channel.

This policy is not a promise that the current codebase is production-ready. The repository is currently in bootstrap state.
