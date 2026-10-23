# ADR-002: Containerized web product and GitHub release pipeline

- Status: Accepted for the first internal release target
- Date: 2026-10-23
- Decision owners: Emergenzix maintainers

## Context

Emergenzix has a Next.js frontend and a FastAPI backend. Running each service manually creates avoidable differences between a developer workstation and an AWS host. The first release should be repeatable without introducing native desktop or mobile clients.

## Decision

1. Deliver the first product as a browser-based web application backed by a separate API service.
2. Package the API and web application as separate Docker images and run them together through Docker Compose.
3. Persist SQLite state in a named Docker volume; do not store the database inside the disposable container filesystem.
4. Bind the web interface to localhost by default and do not publish the API port to the host.
5. Use GitHub Actions for backend/frontend checks and container builds. A semantic-version tag publishes versioned images to GitHub Container Registry and creates a GitHub Release.
6. Keep AWS deployment as a separate, explicit step until the target host, access controls, authentication, TLS/reverse proxy, and least-privilege deployment identity are configured.
7. Keep execution in PAPER mode. Containerization does not authorize or enable real-money trading.

## Consequences

- The same image build can be used on a developer machine and on a compatible cloud host.
- The first version can be rolled forward or back by selecting an image tag.
- SQLite and the current API are suitable only for a controlled single-instance baseline; concurrent multi-user production use requires further storage and security review.
- The release workflow can publish images but does not by itself deploy them to AWS.
- A successful build is not a substitute for Codex/AWS runtime acceptance.

## Acceptance boundary

The v0.1.0 release remains unverified until the container stack starts successfully, the API health check passes, a strategy/backtest/paper-order workflow is exercised, and the named data volume is shown to preserve records after container recreation.
