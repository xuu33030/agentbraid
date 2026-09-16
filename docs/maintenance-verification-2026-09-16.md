# Maintenance verification — 2026-09-16

## Scope and decisions

The owner requested substantive maintenance and a reproducible example, not automatic merging
for an application. No new version, tag, or release was created. Package version remains
**0.2.0a2**. Initial main: `0ed8c89`; post-maintenance main:
`97d27bda61be4559d98b404da6768020754a9e26`.

| PR | Decision | Evidence |
| --- | --- | --- |
| [#18](https://github.com/xuu33030/agentbraid/pull/18) | Closed; branch preserved for focused follow-up | Not docs-only: includes model intelligence, schema v5 and a3 release changes. Controlled-runner reproduction shows workspace B receives workspace A's cached catalog because the cache key only identifies the provider. Bundled benchmark entries are empty. |
| [#19](https://github.com/xuu33030/agentbraid/pull/19) | Merged | Workflow uses hosted runners and none of the removed `pip-install` input. Existing compatibility/quality checks passed; resulting main CI also passed. |
| [#20](https://github.com/xuu33030/agentbraid/pull/20) | Merged | Fresh build with hatchling 1.32 produces metadata 2.5. Twine 6.2 rejects the actual distributions; Twine 7.0 validates the same files. This resolves a reproduced build-tool compatibility problem. |
| [#21](https://github.com/xuu33030/agentbraid/pull/21) | Closed | MCP 2 removes `mcp.server.fastmcp`, still imported by the server. [CI 30503594887](https://github.com/xuu33030/agentbraid/actions/runs/30503594887) fails test collection. A separate SDK migration is needed; runtime bound stays `<2`. |

All merge/closure states and closure comments were read back from GitHub. Main's
[CI 35091558856](https://github.com/xuu33030/agentbraid/actions/runs/35091558856) completed successfully.

## Actual local verification

Environment: macOS, Python 3.13.5, Git 2.50.1, Codex CLI 0.144.6. A fresh venv
was used for dependencies; a venv is not a security sandbox. Build/test scripts were inspected
before execution. A separate environment installed the built wheel with full runtime dependencies.

| Command / check | Observed result |
| --- | --- |
| `uv pip install --python .venv/bin/python -e '.[dev]'` | Fresh editable install succeeded |
| `python -m pytest --cov=agentbraid --cov-report=term -q` on initial main | 126 passed; 85.12% coverage |
| `ruff check src tests scripts` | Passed |
| `ruff format --check src tests scripts` | 34 files formatted |
| `mypy src tests scripts` | No issues in 34 files |
| `agentbraid doctor . --json` | Python/Git/Codex binary/workspace checks passed; this does not verify provider configuration or authentication |
| `python -m build` | Built 0.2.0a2 wheel and sdist |
| `python scripts/check_distribution.py`, Twine 6.2 | Failed: `Invalid distribution metadata: '2.5' is not a valid metadata version` |
| Same command, Twine 7.0, same artifacts | Passed metadata, packaged-file and version smoke checks |
| Clean wheel install with runtime dependencies | Passed; CLI reports `agentbraid 0.2.0a2` |
| Real wheel-installed MCP stdio client | initialize, seven-tool discovery, and `list_capabilities` call passed |
| Real wheel-installed Dashboard HTTP | Unauthenticated `/`: 403; bootstrap-authenticated HTML: 200; authenticated `/api/v1/runs`: 200 with empty list |
| `python scripts/check_licenses.py` | Validated 31 runtime dependency licenses |
| `python -m pip_audit . --strict --progress-spinner off` | No known vulnerabilities found at check time |
| `python -m pytest -q` after dependency merges | 126 passed |
| PR18 tests in a separate environment installed from its own checkout | 141 passed; this does not negate the uncovered catalog-cache regression |

Tests emitted one upstream Starlette/AnyIO `BlockingPortal` deprecation warning. An initial
PR18 test run used mismatched installed a2 metadata and failed its version assertion; installing
PR18 in its own environment resolved that harness error. It is not reported as a PR defect.

The Dashboard check is an HTTP/authentication smoke test, **not** a visual/accessibility or
full browser interaction audit. No live Antigravity-host task was exercised. Local provider
configuration initially failed parsing; the real Codex example explicitly used the CLI's
per-invocation `--ignore-user-config` option, without editing global settings.

## Reproducible real workflow

See [`../examples/verified-slug/README.md`](../examples/verified-slug/README.md) for the seed,
executable driver, red/green evidence, isolated task/integration commits, accountable review,
explicit human approval and read-back of the applied result. It also documents two failed
attempts and their causes instead of presenting a curated first-try success.

The example fixes a seeded whitespace bug, not AgentBraid's own source. The maintenance change
needed by the existing release was the verified Twine tooling update; there was no justification
for publishing a new runtime release merely to add this evidence.
