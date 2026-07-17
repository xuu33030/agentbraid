# Roadmap

AgentBraid uses public milestones and issue-first development. Priorities may change when
provider interfaces or policies change.

## v0.1.0 Alpha

- [x] Repository scaffold and governance
- [x] MCP contracts and durable SQLite state
- [x] Codex lead session adapter
- [x] Antigravity host-mediated plugin
- [x] Task DAG and capability routing
- [x] Git worktree integration
- [x] Provider-policy and security guards
- [x] Cross-platform CI and packaging
- [x] Documentation and examples
- [x] Alpha release readiness

## v0.2.0 Alpha

- [x] Local authenticated run Dashboard
- [x] Cross-workspace run history from one state database
- [x] Provider usage, cache, reasoning, attempt, and retry visualization
- [x] Cross-process cancellation and reviewed apply controls

## After v0.2

- Runtime capability evaluation and adaptive scoring
- Additional provider adapters using explicitly supported programmatic interfaces
- Streamable HTTP transport with local authentication
- Exportable review reports
- MCP Apps view when the active host advertises extension support
- Stable MCP contracts and migration tooling

## Non-goals for v0.1

- Launching Antigravity CLI as a subprocess
- Reading or forwarding provider OAuth credentials
- Sharing one user's subscription with other users
- Hosted multi-tenant orchestration
- Automatic push, deployment, or production mutation
