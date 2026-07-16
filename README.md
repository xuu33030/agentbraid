# AgentBraid

**Braid multiple agents into one accountable workflow.**

AgentBraid is a local-first MCP orchestration server that uses Codex as the lead planner and
integrator while an MCP host can execute specialist tasks. The first public alpha is designed
for Antigravity as the user-facing host and Codex CLI as the background engineering worker.

> [!WARNING]
> AgentBraid is pre-release software. MCP contracts and persisted state may change before 1.0.

## Why AgentBraid?

- Keep one accountable Codex lead for planning, routing, integration, and review.
- Delegate bounded tasks to the active MCP host without launching or impersonating that host.
- Isolate mutating Codex work in Git worktrees and integrate verified commits in DAG order.
- Record task decisions, retries, reviews, and outcomes in local SQLite state.
- Reuse each provider through its documented client and the user's own authorization.

## Architecture

```text
User
  |
  v
Official MCP host (Antigravity CLI in v0.1)
  |
  v
AgentBraid MCP server
  |
  +-- Codex lead: plan, route, integrate, review
  +-- Codex workers: isolated worktrees
  +-- Host tasks: claimed and reported by the MCP host
```

AgentBraid never launches `agy`, reads Antigravity credentials, or proxies Google account
access. See [`docs/provider-policy.md`](docs/provider-policy.md) and
[`docs/security-boundaries.md`](docs/security-boundaries.md) for the supported boundary.

## Quick start

Prerequisites:

- Python 3.11+
- Git 2.35+
- Codex CLI signed in with the user's own account
- An MCP-compatible host

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'

agentbraid doctor
agentbraid init .
agentbraid serve
```

On Windows PowerShell, activate the environment with `.venv\Scripts\Activate.ps1`.

For Antigravity CLI, `agentbraid init` writes a workspace-scoped MCP configuration and the
AgentBraid skill under `.agents/`. Restart the host, then invoke `/agentbraid` with a goal.

## Development status

The [`v0.1.0-alpha` roadmap](ROADMAP.md) tracks public implementation work. Contributions are
welcome through issue-first pull requests; see [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Security and provider terms

- Never commit provider credentials, model transcripts, or AgentBraid runtime databases.
- Every user must authenticate directly with each provider and follow its current terms.
- AgentBraid does not bypass quotas, share subscriptions, or expose a provider login to others.
- Model output is untrusted until project validation and review pass.

Report vulnerabilities privately using [`SECURITY.md`](SECURITY.md).

## License

Licensed under the [Apache License 2.0](LICENSE).

AgentBraid is an independent open-source project. It is not affiliated with, endorsed by, or
sponsored by OpenAI or Google.
