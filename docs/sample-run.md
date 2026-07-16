# Redacted sample run

[`../examples/redacted-run.json`](../examples/redacted-run.json) is a synthetic completed
`RunSnapshot`. The test suite parses it with the current Pydantic schema so examples cannot drift
silently from the MCP contract.

The sample uses:

- placeholder workspace and state paths containing `REDACTED`
- synthetic run and thread identifiers
- repeated hexadecimal digits instead of a real commit SHA
- no provider credentials, raw JSONL events, or model transcript

## Start request

The host starts one integration-delivery run:

```json
{
  "goal": "Add a documented health endpoint without changing authentication. [REDACTED]",
  "host_model": "antigravity-auto",
  "constraints": [
    "Do not push or deploy.",
    "Keep existing API behavior unchanged."
  ],
  "delivery_mode": "integration_branch"
}
```

The persisted request adds the configured absolute workspace after validating that it matches the
server workspace.

## Plan and routing

The Codex lead returns a versioned DAG with two tasks:

```text
inspect-api (codex, read-only)
    └── implement-health (host, mutating)
```

Each task includes the final executor and a persisted assignment rationale. The host must not
claim `inspect-api`; AgentBraid runs it through Codex before making `implement-health` ready.

## Host evidence

The synthetic host result records bounded files, a passing command, confidence, and the task
branch `HEAD`:

```json
{
  "outcome": "succeeded",
  "summary": "Implemented and documented the health endpoint.",
  "changed_files": [
    "src/sample_app/health.py",
    "tests/test_health.py",
    "docs/api.md"
  ],
  "validations": [
    {
      "command": "python -m pytest tests/test_health.py -q",
      "passed": true,
      "output": "2 passed"
    }
  ],
  "confidence": 0.95,
  "commit_sha": "1111111111111111111111111111111111111111",
  "artifacts": []
}
```

AgentBraid verifies the commit, integrates it in DAG order, resumes the Codex lead, and persists
the approved final summary. The run then becomes `completed` but remains unapplied.

## What is intentionally absent

The sample does not imply that AgentBraid stores or exposes:

- provider OAuth tokens, cookies, keyring data, or API keys
- raw Codex JSONL streams
- Antigravity conversation history
- automatic push, deployment, or production access

For the exact host calls, see [`host-walkthrough.md`](host-walkthrough.md). For redaction limits,
see [`security-boundaries.md`](security-boundaries.md).

