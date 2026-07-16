# Getting started

This guide installs AgentBraid into a dedicated Python environment, connects it to one target Git
repository, and starts a first Antigravity-hosted run. AgentBraid v0.1 is local-only alpha
software: it creates local branches and worktrees but never pushes or deploys.

## Prerequisites

- Python 3.11, 3.12, or 3.13
- Git 2.35 or newer
- Codex CLI on `PATH`, authenticated by running `codex login` yourself
- Antigravity CLI, authenticated in the official client with your own account
- A target Git repository with at least one commit and no tracked or untracked changes

AgentBraid does not accept, copy, or forward provider API keys. The Codex child environment is
credential-scrubbed, so the alpha expects an existing Codex CLI login session.

## Install AgentBraid

### From a release wheel

After downloading the alpha wheel from the GitHub release, create a virtual environment that you
will keep on disk:

```bash
python -m venv ~/.venvs/agentbraid
source ~/.venvs/agentbraid/bin/activate
python -m pip install /path/to/agentbraid-0.1.0a1-py3-none-any.whl
agentbraid --version
```

Windows PowerShell:

```powershell
py -3.13 -m venv $HOME\.venvs\agentbraid
& $HOME\.venvs\agentbraid\Scripts\Activate.ps1
python -m pip install C:\path\to\agentbraid-0.1.0a1-py3-none-any.whl
agentbraid --version
```

### From a source checkout

```bash
git clone https://github.com/xuu33030/agentbraid.git
cd agentbraid
python -m venv .venv
source .venv/bin/activate
python -m pip install .
agentbraid --version
```

Use `.venv\Scripts\Activate.ps1` instead of `source` on Windows. Contributors should install
`-e '.[dev]'` and follow [`CONTRIBUTING.md`](../CONTRIBUTING.md).

Do not remove the selected virtual environment after initialization. AgentBraid writes its
absolute Python interpreter into the workspace MCP configuration so the host can reliably start
the same installation.

## Check the target repository

Activate the AgentBraid environment, then move to the repository where agents will work:

```bash
cd /path/to/your/git-repository
agentbraid doctor .
```

A healthy check resembles:

```text
[ok] python: 3.13.5
[ok] git: git version 2.50.1
[ok] codex: codex-cli 0.x.y
[ok] workspace: /path/to/your/git-repository
```

Version strings vary. Resolve every `[error]` before continuing. `doctor --json` provides the
same information in machine-readable form.

AgentBraid requires a clean repository before `start_run`. Commit, stash, or remove existing
changes first:

```bash
git status --short
```

## Install the workspace integration

From the target repository:

```bash
agentbraid init .
```

The command creates or updates:

```text
.agents/
├── mcp_config.json
└── skills/
    └── agentbraid/
        └── SKILL.md
```

The MCP entry runs `python -m agentbraid serve` with `AGENTBRAID_WORKSPACE` fixed to the target
repository. Existing unrelated MCP entries are preserved. If an existing AgentBraid entry or
skill differs, inspect it before using `agentbraid init . --force`.

The generated MCP profile contains machine-specific absolute paths. Keep it local unless your
team intentionally manages per-machine profiles. The skill itself contains no credentials.

## Verify Antigravity discovery

Launch Antigravity from the target repository:

```bash
agy
```

Then:

1. Open `/mcp` and confirm the workspace server `agentbraid` is connected.
2. Open `/skills` and confirm `agentbraid` is loaded.
3. If either is missing, reload the MCP profile or restart the CLI after checking the generated
   files.

These locations and commands follow the official
[Antigravity MCP documentation](https://antigravity.google/docs/mcp) and
[Agent Skills documentation](https://antigravity.google/docs/skills).

## Start a first run

Invoke the workspace skill with a bounded goal and explicit constraints:

```text
/agentbraid Add a tested health endpoint. Keep the existing response format, do not change
authentication, and do not push or deploy.
```

The host follows this lifecycle:

1. `start_run` asks the Codex lead for a typed task DAG and persists its thread ID.
2. Codex-assigned tasks run automatically in managed worktrees.
3. Antigravity claims only tasks routed to the host, works in the returned path, and submits typed
   evidence.
4. AgentBraid integrates successful commits in dependency order.
5. The Codex lead resumes for a read-only final review.
6. A successful run stops at a reviewed local integration branch.

See [`host-walkthrough.md`](host-walkthrough.md) for exact MCP payloads and Git requirements.

## Review and apply

Inspect the completed run and integration branch before delivery. Applying is intentionally
separate from the original goal:

```text
Call apply_run for run <RUN_ID> with confirmation apply-reviewed-run.
```

Only do this after the user explicitly approves updating the current local branch. `apply_run`
requires a completed integration-delivery run and performs only a local `git merge --ff-only`.
It does not push or deploy.

## Optional configuration

Workspace settings can be stored in `.agentbraid.toml`:

```toml
[agentbraid]
codex_timeout_seconds = 1800
max_output_bytes = 10485760
max_task_attempts = 2
```

Useful environment overrides include:

- `AGENTBRAID_STATE_DIR`: platform-specific runtime state root
- `AGENTBRAID_CODEX_MODEL`: optional Codex CLI model selection
- `AGENTBRAID_CODEX_TIMEOUT_SECONDS`: provider invocation timeout
- `AGENTBRAID_MAX_OUTPUT_BYTES`: structured provider output limit
- `AGENTBRAID_MAX_TASK_ATTEMPTS`: global retry ceiling

Database and worktree paths must remain outside the target repository and credential-bearing
directories. See [`security-boundaries.md`](security-boundaries.md) before overriding them.

