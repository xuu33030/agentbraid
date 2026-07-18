# Changelog

All notable changes to AgentBraid are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
uses semantic versioning after the alpha contract stabilizes.

## [Unreleased]

## [0.2.0-alpha.2] - 2026-07-18

### Added

- Add bundled English, Traditional Chinese, and Simplified Chinese Dashboard locales with an
  in-app language selector, browser-language detection, and a loopback-host locale preference.
- Start runs directly from the Dashboard with workspace-scoped Codex model, routing, delivery,
  workspace-access, concurrency, retry, timeout, and output-limit controls.
- Generate editable English, Traditional Chinese, and Simplified Chinese run names during the
  existing Codex planning call while preserving the original goal.
- Select run history entries for explicit deletion of their database state and safely removable
  managed worktrees and branches.

### Changed

- Improve narrow-screen Dashboard navigation, task graph sizing, provider usage rows, event
  timelines, and reviewed-apply readiness feedback while preserving explicit delivery confirmation.
- Upgrade SQLite state to schema v4 with sequential migration, localized run names, immutable
  per-run execution settings, and workspace-level Dashboard defaults.
- Upgrade the Python package to `0.2.0a2`; the intended release tag is `v0.2.0-alpha.2`.

### Security

- Revalidate deletion immediately before cleanup and preserve every selected run when its managed
  worktree is dirty, its integration branch is unmerged, or a task branch contains unique work.
- Keep AGY model selection as routing metadata only; the Dashboard launches only the official
  Codex CLI and never launches, controls, or authenticates as Antigravity.

## [0.2.0-alpha.1] - 2026-07-17

### Added

- Add an authenticated loopback Dashboard with cross-workspace run history, task DAGs, durable
  event timelines, capability health, usage charts, cancellation, and reviewed apply controls.
- Attribute provider usage to task attempts and outcomes, expose cache/reasoning subsets without
  double-counting, and calculate objective retry overhead.

### Changed

- Upgrade SQLite state to schema v3 with sequential v1-to-v2-to-v3 migration and workspace
  backfill.
- Upgrade the Python package to `0.2.0a1`; the intended release tag is `v0.2.0-alpha.1`.

### Security

- Bind the Dashboard only to `127.0.0.1` with a single-use bootstrap token, process-lifetime
  session cookie, Host/Origin/CSRF validation, restrictive response headers, and bundled assets.
- Monitor durable cancellation from planning, task, and review invocations so a separate local
  Dashboard process can terminate active Codex work without bypassing the service boundary.

## [0.1.0-alpha.2] - 2026-07-17

### Fixed

- Bind reviewed runs to their original branch and commit before local apply.
- Reject dirty read-only integration worktrees and stale cross-model capability state.
- Propagate cancellation to active Codex subprocesses and serialize final review per run.

### Added

- Persist per-phase Codex token usage and the original delivery target in SQLite schema v2.
- Honor configured Codex parallelism and the global task retry ceiling.

## [0.1.0-alpha.1] - 2026-07-16

### Added

- Local FastMCP stdio server with typed tools for starting, claiming, submitting, inspecting,
  cancelling, and explicitly applying runs.
- Versioned Pydantic contracts, validated task DAGs, durable SQLite state, retries, cancellation,
  capability history, and persisted routing rationale.
- Codex lead and worker adapter using structured `codex exec` JSONL, output schemas, saved thread
  resume, read-only planning and review, and workspace-write task execution.
- Antigravity workspace installer for `.agents/mcp_config.json` and the AgentBraid host skill,
  without launching or impersonating the Antigravity CLI.
- Deterministic mixed-agent routing using task fit, preference, risk, mutation scope, availability,
  cooldown, outcomes, latency, and required capabilities.
- Git integration and task worktrees, DCO-signed local commits, dependency-ordered cherry-picks,
  conflict rollback, Codex final review, and explicit fast-forward-only apply.
- Cross-platform CI for Python 3.11 through 3.13 on Linux, macOS, and Windows, including lint,
  formatting, type checks, coverage, vulnerability audit, runtime-license audit, package checks,
  and clean-wheel smoke installation.
- Installation, architecture, host workflow, troubleshooting, security, routing, worktree, and
  schema-validated redacted example documentation.

### Security

- Reject provider executable substitution, nested AgentBraid runs, credential-bearing runtime
  paths, unverified host commits, merge commits, and implicit delivery.
- Remove credential-like variables from Codex child environments and redact common token, secret,
  credential URL, JWT, and private-key patterns before persistence or surfacing.
- Keep Antigravity as the official authenticated host and never read, copy, or forward Google or
  Antigravity credentials.

### Known limitations

- Alpha MCP schemas and SQLite state may change incompatibly before 1.0.
- Codex tasks execute sequentially in v0.1, and the Antigravity host workflow is the only tested
  mixed-provider integration.
- Integration conflicts require a new bounded repair run; AgentBraid does not force-resolve,
  push, deploy, or publish changes.

[Unreleased]: https://github.com/xuu33030/agentbraid/compare/v0.2.0-alpha.2...HEAD
[0.2.0-alpha.2]: https://github.com/xuu33030/agentbraid/compare/v0.2.0-alpha.1...v0.2.0-alpha.2
[0.2.0-alpha.1]: https://github.com/xuu33030/agentbraid/compare/v0.1.0-alpha.2...v0.2.0-alpha.1
[0.1.0-alpha.2]: https://github.com/xuu33030/agentbraid/compare/v0.1.0-alpha.1...v0.1.0-alpha.2
[0.1.0-alpha.1]: https://github.com/xuu33030/agentbraid/releases/tag/v0.1.0-alpha.1
