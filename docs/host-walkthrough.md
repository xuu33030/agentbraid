# Antigravity host walkthrough

Antigravity is the user-facing MCP host in AgentBraid v0.1. Codex remains the accountable lead:
it creates the global plan, supplies routing preferences, performs Codex-assigned work, and gives
the final review. Antigravity executes only tasks explicitly routed to `host`.

In normal use, invoke `/agentbraid` and let the installed skill follow this protocol. The payloads
below are useful for reviewing tool approvals, diagnosing a run, or implementing another
compatible MCP host.

## 1. Start one run

Call `start_run` once with the complete goal. A schema-valid example is available at
[`../examples/start-run.json`](../examples/start-run.json).

```json
{
  "request": {
    "goal": "Add a tested health endpoint without changing authentication.",
    "host_model": "antigravity-auto",
    "constraints": [
      "Do not push or deploy.",
      "Preserve the existing public API except for the new endpoint."
    ],
    "delivery_mode": "integration_branch"
  }
}
```

Keep the returned `run_id`. The response may already include completed Codex tasks and one or
more ready host tasks because AgentBraid drains ready Codex work before returning.

## 2. Inspect assignments

Use `get_run` and read each task's `executor`, `status`, `dependencies`,
`acceptance_criteria`, and `assignment_rationale`.

Do not execute a task assigned to `codex`. Do not broaden a host task merely because adjacent work
looks useful. If no host task is ready, a dependency may still be running, the run may be ready
for final review, or all remaining work may belong to Codex.

## 3. Claim one host task

Use a stable, non-secret host identifier for the Antigravity session:

```json
{
  "run_id": "<RUN_ID>",
  "host_id": "antigravity-host"
}
```

Call `claim_host_task`. To claim a known ready task, also pass its `task_id`. Claims are atomic;
another host cannot claim the same task concurrently.

The result includes a managed `worktree_path`:

- A mutating task receives its own task branch and isolated worktree.
- A non-mutating task receives the integration worktree and must treat it as read-only.

Never substitute the user's primary checkout or a different path.

## 4. Execute the bounded contract

Follow the returned instructions and acceptance criteria. For a mutating task:

```bash
git -C <WORKTREE_PATH> status --short
# edit only the assigned files and run the required validation
git -C <WORKTREE_PATH> add -- <CHANGED_PATHS>
git -C <WORKTREE_PATH> commit --signoff -m "docs: describe the health endpoint"
git -C <WORKTREE_PATH> rev-parse HEAD
```

The submitted commit must:

- be the assigned task branch `HEAD`
- contain a `Signed-off-by` trailer created with `--signoff`
- contain no merge commit
- stay within the task scope
- have at least one passing validation record for successful mutating work

AgentBraid verifies these conditions before cherry-picking the commit onto the run's integration
branch.

## 5. Submit typed evidence

Call `submit_host_result` with the same `host_id`. A complete payload is in
[`../examples/host-result.json`](../examples/host-result.json).

```json
{
  "run_id": "<RUN_ID>",
  "task_id": "document-health",
  "host_id": "antigravity-host",
  "result": {
    "outcome": "succeeded",
    "summary": "Documented the endpoint and verified the documentation tests.",
    "changed_files": ["docs/api.md"],
    "validations": [
      {
        "command": "python -m pytest tests/test_docs.py -q",
        "passed": true,
        "output": "1 passed"
      }
    ],
    "confidence": 0.94,
    "commit_sha": "1111111111111111111111111111111111111111",
    "artifacts": []
  }
}
```

Use a real local commit SHA; the repeated digit above is synthetic. A successful non-mutating
task must omit `commit_sha`. If work cannot safely complete, submit `blocked` or `failed` with a
concise redacted error instead of claiming success.

Submitting a result may trigger newly ready Codex tasks and final review before the tool returns.

## 6. Repeat or inspect completion

Call `get_run`, then claim the next host task. Stop claiming when:

- no host task is ready
- the run is `blocked`, `cancelled`, or `failed`
- the run is `completed`

The redacted completed snapshot in
[`../examples/redacted-run.json`](../examples/redacted-run.json) is validated against the current
Pydantic contract in the test suite.

## 7. Apply only after separate approval

`completed` means the integration candidate passed Codex lead review. It does not mean the user's
current branch changed.

After showing the reviewed result, obtain explicit user approval and call:

```json
{
  "run_id": "<RUN_ID>",
  "confirmation": "apply-reviewed-run"
}
```

`apply_run` performs a local fast-forward only. AgentBraid has no automatic push or deployment
path. Never infer apply approval from the initial goal.

## MCP tool reference

| Tool | Purpose | Important behavior |
| --- | --- | --- |
| `start_run` | Plan, route, persist, and start a run | Requires a clean configured Git workspace |
| `claim_host_task` | Atomically claim one ready host task | Returns only tasks routed to `host` |
| `submit_host_result` | Submit typed result, evidence, and optional commit | Verifies mutating commits before integration |
| `get_run` | Inspect the durable run snapshot | Read-only and idempotent |
| `cancel_run` | Cancel active work without deleting history | Destructive annotation; idempotent |
| `list_capabilities` | Inspect redacted executor health | Never returns credentials |
| `apply_run` | Fast-forward the current local branch | Destructive; requires literal confirmation |

## Host boundary

The host must never ask AgentBraid to launch `agy`, expose Google credentials, push, deploy, or
reuse another user's provider session. Review [`provider-policy.md`](provider-policy.md) and
[`security-boundaries.md`](security-boundaries.md) before adapting this protocol to another host.

