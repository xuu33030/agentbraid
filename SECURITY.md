# Security policy

## Supported versions

AgentBraid is currently alpha software. Security fixes are applied to the latest development
branch and most recent pre-release only.

## Reporting a vulnerability

Do not open a public issue for credential exposure, command injection, sandbox escape, unsafe
worktree behavior, or provider-authentication bypass.

Use GitHub's private vulnerability reporting feature for `xuu33030/agentbraid`. If that feature
is temporarily unavailable, contact the maintainer through the private address listed on the
GitHub profile and include:

- affected version and platform
- reproduction steps or a minimal proof of concept
- expected and observed security boundary
- whether credentials or remote systems may be affected

Do not include real credentials or personal model transcripts. You should receive an initial
acknowledgement within seven days.

## Security boundaries

AgentBraid must never:

- read or forward provider OAuth tokens
- invoke Antigravity CLI as a background worker
- expose one user's subscription to another user
- run mutating worker tasks outside an explicit workspace
- push, deploy, or destroy worktrees without an explicit caller action

