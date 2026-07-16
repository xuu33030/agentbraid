# Contributing to AgentBraid

Thank you for helping make multi-agent work more accountable and interoperable.

## Before opening code

- Search existing issues and the `v0.1.0-alpha` milestone.
- Small documentation and test fixes may open a pull request directly.
- Features, behavior changes, and new provider integrations require an accepted issue first.
- Breaking architecture or security changes require an RFC under `docs/rfcs/`.
- Do not submit code that scrapes, forwards, shares, or bypasses provider authentication.

Maintainers may reserve policy-sensitive work with the `maintainer-only` label.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
pytest
ruff check .
ruff format --check .
mypy
```

## Pull requests

1. Fork the repository and create a focused branch.
2. Link the issue using `Closes #...` when applicable.
3. Add or update tests for behavior changes.
4. Document provider-policy and security implications.
5. Keep credentials, runtime databases, worktrees, and model transcripts out of commits.
6. Complete the pull request checklist and wait for required checks.

External pull requests are squash-merged by default.

## Developer Certificate of Origin

Every commit must certify the [Developer Certificate of Origin](DCO). Sign off with:

```bash
git commit --signoff -m "feat: describe the change"
```

The sign-off records that you have the right to contribute the work under Apache-2.0.

## Commit style

Use Conventional Commit prefixes such as `feat`, `fix`, `docs`, `test`, `refactor`, `chore`,
and `ci`. Keep each commit reviewable and avoid unrelated formatting changes.

## Conduct and security

Participation is governed by [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md). Report
vulnerabilities through the private process in [`SECURITY.md`](SECURITY.md), not a public issue.

