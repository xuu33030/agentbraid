# Changelog

All notable changes to AgentBraid are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
uses semantic versioning after the alpha contract stabilizes.

## [Unreleased]

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

[Unreleased]: https://github.com/xuu33030/agentbraid/compare/v0.1.0-alpha.1...HEAD
[0.1.0-alpha.1]: https://github.com/xuu33030/agentbraid/releases/tag/v0.1.0-alpha.1
