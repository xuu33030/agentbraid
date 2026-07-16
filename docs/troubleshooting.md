# Troubleshooting

Start with these commands in the target repository:

```bash
agentbraid --version
agentbraid doctor .
git status --short
```

Do not attach credentials, the runtime SQLite database, raw model transcripts, or provider config
directories to a public issue.

## `codex` is not found

`doctor` requires the official `codex` executable on `PATH`.

1. Run `codex --version` in the same shell.
2. Install or update Codex CLI through its official distribution.
3. Run `codex login` directly and complete provider authentication yourself.
4. Restart Antigravity if its environment was created before the `PATH` change.

AgentBraid intentionally rejects wrapper paths and binaries whose basename is not `codex` or
`codex.exe`.

## The workspace check fails

Run `agentbraid doctor /absolute/path/to/repository`. The target must be inside a Git repository
with an existing commit. Initialize and commit the repository before starting a run.

## `start_run` says the workspace is dirty

AgentBraid refuses to create integration worktrees when the target checkout has tracked or
untracked changes. Commit, stash, or remove them, then verify:

```bash
git status --porcelain
```

The command must produce no output.

## Antigravity shows the MCP server as disconnected

1. Open `.agents/mcp_config.json` and confirm its Python executable still exists.
2. Activate that environment and run `python -m agentbraid --version`.
3. Confirm `AGENTBRAID_WORKSPACE` points to the currently opened repository.
4. Open `/mcp` in Antigravity and inspect connection logs or reload the server.
5. Re-run `agentbraid init .` after moving the virtual environment or repository.

Running `python -m agentbraid serve` manually appears to wait because it is a stdio protocol
server. Stop it with `Ctrl+C`; use Antigravity's `/mcp` logs for protocol diagnostics.

## `/agentbraid` or the skill is missing

Confirm `.agents/skills/agentbraid/SKILL.md` exists in the repository opened by Antigravity, then
use `/skills` to reload or inspect workspace skills. Antigravity's current workspace skill path is
documented at [antigravity.google/docs/skills](https://antigravity.google/docs/skills).

If a custom file already occupied that path, `agentbraid init` stops instead of overwriting it.
Review the difference, then use `--force` only when replacing it is intended.

## `claim_host_task` returns no task

This is not always an error. Call `get_run` and check:

- whether ready tasks are assigned to Codex
- whether a host task is waiting on dependencies
- whether another host already claimed it
- whether the run is reviewing, completed, blocked, failed, or cancelled

Keep one stable `host_id` through claim and submit. A result from a different identifier is
rejected.

## A successful host result is rejected

For mutating tasks, verify all of the following:

```bash
git -C <WORKTREE_PATH> status --short
git -C <WORKTREE_PATH> rev-parse HEAD
git -C <WORKTREE_PATH> show -s --format=%B HEAD
git -C <WORKTREE_PATH> rev-list --parents -n 1 HEAD
```

- `commit_sha` exactly matches `HEAD`
- the commit has a `Signed-off-by` trailer
- the commit is not a merge commit
- the result includes at least one validation and every validation passed
- the task was claimed by the same host and is still running

Do not create an empty success commit or submit a commit for a non-mutating task.

## Integration is blocked by a conflict

AgentBraid restores the integration branch to its prior commit and preserves the failed task
worktree. Inspect the conflict locally, but do not force-reset or delete managed branches. Start a
new bounded repair run or resolve the task under explicit user direction.

## Final review is blocked

Call `get_run` and read `final_summary` and `error`. The Codex lead may have reported a failed
validation or an error-level finding. AgentBraid does not auto-apply a candidate that the lead did
not approve. Address the finding in a new run rather than bypassing review state.

## `apply_run` fails

Check that:

- the run status is `completed`
- `delivery_mode` is `integration_branch`, not `report_only`
- the literal confirmation is `apply-reviewed-run`
- the current workspace can fast-forward to the integration branch
- the target checkout remains clean

AgentBraid never performs a merge commit, force update, push, or deployment as a fallback.

## Provider timeout, quota, or unavailable errors

1. Verify `codex exec --help` and a normal Codex CLI request work outside AgentBraid.
2. Check the user's provider quota in the official client.
3. Re-authenticate with `codex login` if the official session expired.
4. Increase `codex_timeout_seconds` only for legitimately long local work.
5. Inspect `list_capabilities` for `constrained`, `cooldown`, or `unavailable` state.

Do not solve authentication failures by putting API keys into the goal or workspace. AgentBraid
removes credential-like environment variables from Codex children by design.

## Runtime state and recovery

Runtime state and managed worktrees live under the platform-specific user state directory unless
`AGENTBRAID_STATE_DIR` overrides it. Keep that path outside the target repository. If recovery is
needed, stop the host and back up the entire state directory before inspecting it.

Do not edit the SQLite database manually or publish it in a bug report. It contains task prompts,
redacted results, local paths, and run history even though common secret patterns are filtered.

## Reporting a reproducible bug

Include only:

- `agentbraid --version`
- redacted `agentbraid doctor --json` output
- operating system and Python version
- the stable AgentBraid error code and redacted message
- a synthetic minimal repository or reproduction steps
- the run ID, if it is not sensitive

Use the private process in [`../SECURITY.md`](../SECURITY.md) for credential exposure or security
boundary failures.

