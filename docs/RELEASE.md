# Versioning, CI/CD, and container releases

## Current target

- Product version target: 0.1.0 (see the root VERSION file).
- Runtime mode: PAPER only; LIVE order execution is not implemented.
- Packaging: Next.js web container + FastAPI API container + a named SQLite data volume.
- Local default: the web interface binds to 127.0.0.1:3000; the API is only exposed inside the Compose network.
- A version target is not a verified release. Do not publish the v0.1.0 tag until Codex/AWS acceptance has been completed and the relevant GitHub Actions checks are green.

## CI responsibilities

GitHub Actions runs the backend tests, frontend type-check/build, and container image builds on relevant changes. Pull requests and main-branch changes should reveal packaging regressions before a release is tagged.

## CD responsibilities

Pushing a version tag matching v*.*.* starts the release-containers workflow. **Publishing is blocked until the validation job passes.** Before publishing, it:

1. Verifies that the tag points to a commit already contained in main.
2. Confirms the tag version matches VERSION, backend/pyproject.toml, backend/emergenzix_api/__init__.py, and web/package.json.
3. Compiles the backend and runs the backend test suite.
4. Runs the web type-check and production build.
5. Builds both Docker images without publishing them.

Only after all validation steps pass does the separate publish job push:

- ghcr.io/huliye24/emergenzix-api
- ghcr.io/huliye24/emergenzix-web

The workflow creates a GitHub Release with generated notes. Each image receives semantic-version, v-prefixed tag, and latest tags. The GitHub Container Registry package visibility may need to be changed in GitHub package settings if public pulls are desired.

A pull-request trigger also exercises the release validation steps when the release workflow or version metadata changes; it never publishes images from a pull request.

This is container delivery, not automatic AWS deployment. AWS deployment should be added after the target instance, access-control design, persistent storage, reverse proxy/TLS, and authentication approach are confirmed. Do not store long-lived AWS keys in the repository; prefer GitHub Actions OIDC with a least-privilege AWS role when the deployment workflow is introduced.

## Release checklist

1. Run Codex verification locally and on the intended AWS development environment.
2. Confirm backend tests, web type-check/build, and container-build CI succeed.
3. Confirm data persists when the API container is recreated.
4. Confirm the public web port is not exposed beyond the intended network.
5. Align VERSION, backend/emergenzix_api/__init__.py, backend/pyproject.toml, and web/package.json.
6. Create and push the version tag only after acceptance:

```bash
git tag -a v0.1.0 -m Emergenzix-v0.1.0
git push origin v0.1.0
```

7. Inspect the generated GitHub Release and both container image tags.

Never treat a green build or a successful image push as proof of strategy profitability, trading safety, or production readiness.
