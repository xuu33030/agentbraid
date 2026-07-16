# Provider policy

Last reviewed: 2026-07-16

AgentBraid integrates only through documented provider interfaces and a user's own account.
This document describes the project boundary; it is not legal advice.

## Antigravity

Supported in v0.1:

- The user launches and signs in to the official Antigravity CLI.
- Antigravity loads AgentBraid as a local MCP server and workspace skill.
- The active Antigravity agent claims typed host tasks and submits typed results.

Not supported:

- AgentBraid launching `agy`, including `agy --print`
- reading or copying Antigravity keyring or OAuth data
- using one user's Antigravity login from another process or for another user

Official references:

- [Model Context Protocol](https://antigravity.google/docs/mcp)
- [Agent Skills](https://antigravity.google/docs/skills)
- [Plugins and skills](https://antigravity.google/docs/cli-plugins)
- [FAQ](https://antigravity.google/docs/faq)

The FAQ currently says third-party software may not use an Antigravity login to access the
service. The official MCP and Skill documentation supports local workspace tools under
`.agents/`. Host-mediated MCP therefore keeps Antigravity as the official authenticated client
and AgentBraid as a local tool; AgentBraid never consumes the Antigravity login itself.

## Codex

AgentBraid uses the documented non-interactive Codex CLI interface. Each user signs in through
Codex directly. AgentBraid stores Codex thread identifiers and redacted structured task output,
not credentials. The v0.1 child environment removes API-key variables, so users authenticate with
an existing `codex login` session rather than asking AgentBraid to forward a key.

Official references:

- [Non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)
- [`codex exec` reference](https://learn.chatgpt.com/docs/developer-commands?surface=cli#cli-codex-exec)

The current Codex manual documents `codex exec`, read-only and workspace-write sandboxes, JSONL,
`--output-schema`, `--output-last-message`, session resume, and reuse of saved CLI authentication.
Those are the interfaces used by the v0.1 adapter.

## Maintenance

Provider behavior and terms can change. Review this file before each release. A policy change
that invalidates an adapter is a security issue and may disable that adapter without a normal
deprecation period.

The `v0.1.0-alpha.1` review on 2026-07-16 found no change requiring either adapter to be disabled.
This is a technical boundary review, not legal advice or a guarantee of provider approval.
