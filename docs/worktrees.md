# Worktree isolation and delivery

AgentBraid keeps model-written changes away from the user's checked-out branch until an explicit
delivery action.

## Local branches and paths

- Each run receives an integration branch named `agentbraid/integration/<run>`.
- Each mutating task receives a branch named `agentbraid/tasks/<run>/<task>`.
- Integration and task worktrees live under the configured state directory, outside the source
  repository. AgentBraid rejects worktree roots inside the repository.
- Non-mutating tasks inspect the integration worktree in read-only mode so they see completed
  dependency changes.

## Commit and integration gates

Codex workers return structured validation evidence but do not commit directly. AgentBraid stages
their bounded changes and creates a local DCO-signed commit only after every reported validation
passes. Host workers create their own local signed-off commit in the assigned task worktree.

Before integration, AgentBraid verifies that the submitted commit is the task worktree's `HEAD`,
contains no merge commits, and includes a `Signed-off-by` trailer. Commits are cherry-picked onto
the integration branch only after dependency tasks have succeeded. A conflict resets the
integration branch to its prior commit and leaves the task worktree intact for recovery.

## Review and apply

The Codex lead resumes its planning session for a read-only final review of the integrated
candidate. Only an approved review moves a run to `completed`.

`apply_run` is a separate, explicit action. It performs a local `--ff-only` merge into the user's
current branch. AgentBraid never pushes or deploys, and report-only runs cannot be applied.
