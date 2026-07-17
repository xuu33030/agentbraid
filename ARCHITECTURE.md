# Architecture

## Principles

1. **One accountable lead.** Codex owns the global plan, routing rationale, integration, and
   final review for a run.
2. **Host mediation.** The active MCP host claims explicitly assigned tasks and reports typed
   results; AgentBraid does not control or authenticate as that host.
3. **Isolation before concurrency.** Mutating worker tasks use separate Git worktrees and local
   commits before integration.
4. **Durable orchestration.** Runs and tasks survive process restarts through SQLite state.
5. **Least privilege.** Planning is read-only, writes are workspace-scoped, and remote delivery
   is always explicit.

## Components

```mermaid
flowchart TB
    subgraph clients["Official provider clients"]
        host["Antigravity CLI<br/>user-facing MCP host"]
        codex["Codex CLI<br/>authenticated user session"]
    end

    subgraph agentbraid["AgentBraid service and MCP process"]
        mcp["FastMCP stdio server"]
        scheduler["Planner and deterministic router"]
        integrator["Worktree and integration manager"]
        store["Durable SQLite state"]
    end

    subgraph dashboard_process["Independent local Dashboard process"]
        dashboard["Authenticated loopback Web App"]
    end

    subgraph repository["Local Git repository boundary"]
        primary["User's current branch"]
        integration["agentbraid/integration/<run>"]
        tasks["agentbraid/tasks/<run>/<task>"]
    end

    host <-->|"typed MCP tools"| mcp
    mcp --> scheduler
    scheduler -->|"codex exec --json"| codex
    scheduler --> store
    scheduler --> integrator
    dashboard -->|"start / settings"| scheduler
    dashboard --> store
    dashboard -->|"cancel / safe cleanup / explicit apply"| integrator
    host -->|"edits only claimed worktree"| tasks
    codex -->|"bounded worker edits"| tasks
    integrator --> tasks
    integrator --> integration
    integration -.->|explicit ff-only apply| primary
```

### MCP server

Exposes the public run lifecycle: start, claim host work, submit host results, inspect, cancel,
list capabilities, and apply an integration branch.

### Codex lead

Runs in a read-only sandbox to produce a validated task DAG. Its thread ID is persisted and
resumed for integration decisions and final review.

### Codex workers

Execute bounded tasks in per-task worktrees. A successful mutating task must return validation
evidence and a local commit SHA. Branch naming, conflict recovery, and explicit delivery are
documented in `docs/worktrees.md`.

### Scheduler

Selects `codex` or `host` using task fit, historical outcomes, availability, latency, and risk.
The scoring policy is deterministic and its rationale is stored with the assignment. The fixed
v0.1 weights and hard availability rules are documented in `docs/routing.md`.

### State store

SQLite records runs, tasks, dependencies, attempts, events, capabilities, worktrees, review
findings, localized run names, immutable execution snapshots, and workspace settings. Schema v4
retains schema v3 workspace and provider-usage attribution while adding the Dashboard execution
metadata. Runtime state lives outside the repository by default.

### Local Dashboard

`agentbraid dashboard` is a separate process that serves bundled HTML, CSS, and JavaScript on an
authenticated `127.0.0.1` session. It lists runs from one active state database, derives token
breakdowns without double-counting cached or reasoning subsets, and invokes the same service and
worktree safety checks for run creation, cancellation, cleanup, and explicit apply. The Dashboard
may launch the official Codex CLI with a selected model, but it cannot execute host work or launch
Antigravity; AGY model values remain routing metadata for the authenticated MCP host.

Each run stores its resolved execution settings so later workspace-default changes cannot alter an
existing run's model, routing, worktree, timeout, retry, or output-limit contract. Runtime path
changes require a Dashboard and MCP restart. Selected-run cleanup is fail-closed and revalidates
dirty worktrees and branch uniqueness immediately before deleting the database record.

Because the Dashboard can cancel a run owned by the MCP process, every planning, worker, and
review invocation races provider completion against the durable cancelled status. Same-process
cancellation remains immediate; cross-process cancellation is observed through SQLite and then
propagated to the Codex subprocess.

## Run lifecycle

```mermaid
sequenceDiagram
    actor User
    participant Host as Antigravity CLI
    participant Braid as AgentBraid MCP
    participant Codex as Codex CLI
    participant Git as Git worktrees

    User->>Host: Submit bounded goal
    Host->>Braid: start_run(request)
    Braid->>Git: Verify clean target repository
    Braid->>Codex: Read-only structured planning
    Codex-->>Braid: Versioned task DAG and lead thread ID
    Braid->>Git: Create integration worktree
    Braid->>Braid: Route tasks and persist rationale

    loop Ready tasks
        alt Task assigned to Codex
            Braid->>Git: Create isolated task worktree
            Braid->>Codex: Execute bounded task
            Braid->>Git: Validate, sign off, and integrate commit
        else Task assigned to host
            Host->>Braid: claim_host_task(run_id)
            Braid-->>Host: Task contract and worktree path
            Host->>Git: Edit, validate, and create signed-off commit
            Host->>Braid: submit_host_result(result, evidence, SHA)
            Braid->>Git: Verify and integrate commit
        end
    end

    Braid->>Codex: Resume lead for read-only final review
    Codex-->>Braid: Approval or findings
    Braid-->>Host: Completed or blocked run snapshot
    User->>Host: Separately approve local apply
    Host->>Braid: apply_run(..., "apply-reviewed-run")
    Braid->>Git: Fast-forward current branch only
```

The optional Dashboard can start the same persisted lifecycle, issue cancellation, safely delete
terminal history, or perform the same explicitly confirmed apply operation. Hybrid runs still need
Antigravity to claim host-assigned work; the Dashboard does not replace it as the MCP host.

```text
created -> planning -> running -> integrating -> reviewing -> completed
                              \-> blocked
                    \-> cancelled
```

Each task follows:

```text
pending -> ready -> running -> succeeded
                         \-> retrying -> running
                         \-> failed
                         \-> cancelled
```

## Trust boundaries

- Prompts, model output, web content, and repository instructions are untrusted input.
- Provider credentials stay inside official provider clients.
- AgentBraid passes goals and task context, never raw authentication material.
- Child workers receive a recursion marker and cannot create nested AgentBraid runs.
- Pushes, deployments, and destructive cleanup require an explicit caller action.
- Provider child environments are credential-scrubbed, persisted strings are redacted, and
  runtime paths cannot target credential-bearing directories. See `docs/security-boundaries.md`.

## Public compatibility

MCP schemas are versioned independently from the package. During alpha, incompatible schema
changes are allowed when documented in `CHANGELOG.md`.
