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

- https://antigravity.google/docs/mcp
- https://antigravity.google/docs/cli-plugins
- https://antigravity.google/docs/faq

The FAQ currently says third-party software may not use an Antigravity login to access the
service. Host-mediated MCP keeps Antigravity as the official client and AgentBraid as a tool.

## Codex

AgentBraid uses the documented non-interactive Codex CLI interface. Each user signs in through
Codex directly. AgentBraid stores Codex thread identifiers and structured task output, not
credentials.

Official references:

- https://developers.openai.com/codex/noninteractive
- https://developers.openai.com/codex/sdk

## Maintenance

Provider behavior and terms can change. Review this file before each release. A policy change
that invalidates an adapter is a security issue and may disable that adapter without a normal
deprecation period.

