# CI Code Gate

## Goal

Every pull request targeting \`main\` must complete backend tests, web build, and both Docker image builds before it is eligible to merge. These checks are validation only: they do not deploy services or enable live trading.

## Workflows

The following workflows now run for every pull request targeting \`main\`, regardless of which files changed:

- **Backend tests / test** — compile Python modules and run the backend API test suite.
- **Web build / build** — install web dependencies, run type-checking, and build the web app.
- **Container build / Build API image** — build the backend Docker image without publishing it.
- **Container build / Build web image** — build the web Docker image without publishing it.

Push-triggered runs retain their path filters to avoid unnecessary Actions usage. Pull-request checks intentionally do not use path filters so a required check cannot be silently skipped because a PR only changed documentation, configuration, or another path.

## Required repository setting

Workflow files can run checks, but they cannot by themselves prevent a merge. A repository administrator must enable required status checks for the \`main\` branch.

In GitHub, open **Settings → Rules → Rulesets** (or **Settings → Branches → Branch protection rules**, depending on the repository settings), then create or edit a rule targeting \`main\`:

1. Require a pull request before merging.
2. Require status checks to pass before merging.
3. Select all four checks listed above.
4. If available, enable **Require branches to be up to date before merging**.
5. Save the rule and verify that a test PR cannot merge while a required check is failing.

The connected GitHub integration used to edit repository files does not have permission to read or change branch-protection settings, so this one-time repository setting must be completed by an authorized repository administrator in GitHub's UI.

## Scope and safety

- This gate does not publish container images.
- Versioned image publication remains a separate release workflow.
- AWS deployment remains operator-controlled.
- No live trading or exchange order permissions are part of CI.
