# Security boundaries

AgentBraid treats user goals, repository files, model output, command output, and MCP arguments as
untrusted input. Its v0.2 controls reduce accidental credential exposure and prevent models from
silently delivering changes outside the reviewed local workflow.

## Provider processes

- AgentBraid can launch only an executable whose basename is `codex` (or `codex.exe`) through the
  Codex adapter. It never uses a shell to construct provider or Git commands.
- The Codex child environment removes variables whose names indicate keys, tokens, credentials,
  passwords, authorization material, JWTs, or secrets. This includes Google, Antigravity,
  GitHub, cloud-provider, OpenAI, and Codex API-key variables.
- Codex authentication therefore uses the user's existing `codex login` session. Raw API
  keys are not forwarded by AgentBraid.
- Every child receives `AGENTBRAID_CHILD=1`; a nested AgentBraid server or run fails closed.
- AgentBraid never launches `agy` and never reads Antigravity or Google credential storage.

## Filesystem and Git

- The configured workspace cannot be a known credential-bearing directory such as `.ssh`,
  `.gnupg`, `.aws`, `.codex`, `.gemini`, or the Google Cloud configuration directory.
- Runtime state, the SQLite database, and managed worktrees must live outside the workspace and
  outside those credential directories.
- Mutating tasks run only in managed Git worktrees. Submitted commits must be the assigned task
  branch `HEAD`, contain a DCO signoff, and contain no merge commits.
- Integration conflicts restore the prior integration commit. Failed worktrees remain available
  for local recovery; AgentBraid does not destructively clean them up.

## Persistence and logs

Before prompts, structured results, event payloads, capability metadata, error details, and commit
titles are persisted or surfaced, AgentBraid redacts common secret assignments, bearer tokens,
credential-bearing URLs, known provider-token prefixes, JWTs, and private-key blocks. Raw Codex
JSONL events are not stored.

Redaction is defense in depth, not a secret manager. Users must not place real credentials in
goals, repositories, task results, or bug reports.

## Local Dashboard

- `agentbraid dashboard` binds only to IPv4 loopback (`127.0.0.1`) and does not accept a remote
  bind address.
- A random bootstrap token establishes one process-lifetime, HttpOnly, SameSite-strict browser
  session. The bootstrap token is single-use and neither token is persisted.
- Every state-changing request must match the Dashboard origin and provide the per-process CSRF
  token. Invalid Host headers, unauthenticated requests, and cross-origin mutations fail closed.
- Dashboard responses use a restrictive content security policy, deny framing, disable caching,
  and load only assets bundled in the AgentBraid wheel. Repository and model text is rendered as
  text rather than executable markup.
- The Dashboard reads only the active state database. Its all-project view does not scan the
  filesystem or discover unrelated custom databases.
- Cancellation is persisted first. MCP-side planning, worker, and review invocations monitor the
  durable status and terminate their Codex subprocess when another local AgentBraid process
  cancels the run.
- The Dashboard may start the same workspace-scoped service lifecycle and launch only the official
  Codex CLI. It cannot claim or execute host tasks, launch or control Antigravity, read provider
  credentials, push, or deploy. The AGY model field is routing metadata, not a provider control.
- Run deletion requires an exact confirmation and revalidates every managed artifact. Active runs,
  dirty worktrees, unmerged integration branches, unique task patches, moved workspaces, and
  unverifiable paths preserve both local artifacts and the corresponding database record.
- Workspace settings are restricted to workspaces already represented by the active database or
  the Dashboard's initial Git workspace. Environment-variable values remain authoritative;
  database and state paths are not editable in the browser.

## Delivery

- Final task success produces a local integration branch, not a push or deployment.
- The Codex lead must approve a read-only final review before the run can be delivered.
- `apply_run` is marked destructive in MCP metadata, accepts only integration-delivery runs, and
  requires the literal confirmation `apply-reviewed-run`.
- Apply performs only a local `git merge --ff-only`. AgentBraid has no automatic push or deploy
  path.
