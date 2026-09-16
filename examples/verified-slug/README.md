# Verified whitespace-fix example

This is a real Codex-backed run of AgentBraid **0.2.0a2**, not the synthetic
[`redacted-run.json`](../redacted-run.json) schema example. The target is a deliberately
small, seeded bug; it is not a claim of solving an external project's issue.

## Recorded result

Run `248653d9bfdb435a989ae5c988bb2622` on 2026-09-16 used Codex CLI 0.144.6,
model `gpt-5.6-sol`, macOS and Python 3.13.5:

1. The real Codex planning call produced one bounded implementation task.
2. AgentBraid created separate task and integration Git worktrees outside the primary checkout.
   Git worktrees isolate changes; they are **not a security sandbox**. The adapter requested
   Codex `workspace-write` for implementation and `read-only` for planning/review.
3. The worker added regressions first, observed failures, then added `.strip()` to `slugify`.
   The original lowercase/internal-space behavior remained covered.
4. AgentBraid committed the validated task with the owner's authorized DCO sign-off and
   integrated it. The accountable Codex lead approved after reviewing the complete snapshot.
5. A separate local verification reran all six tests and checked the complete two-file diff.
   An independent replay also ran the final tests against the original implementation (failure)
   and against the fix (success).
6. The primary branch remained at `0dbe519a7d6ad41acf6745fafb465704060cfbf1` until the
   human explicitly confirmed **「同意套用並記錄人工確認」** after seeing the review results.
7. `apply_run(run_id, "apply-reviewed-run")` then fast-forwarded the local demo branch to
   `8623be1314c61082f2dedda5ced96483073be78a`. HEAD and clean status were read back.
   No push, deployment, tag, or release was performed by the example.

Evidence:
- [`evidence/run.json`](evidence/run.json): actual plan, typed task evidence, usage, final review
  summary, pre-apply branch check, human confirmation and apply result. This is an evidence
  bundle, **not** a raw `RunSnapshot`; private absolute paths are replaced with `<WORKSPACE_ROOT>`.
- [`evidence/change.diff`](evidence/change.diff): exact final two-file diff.
- [`evidence/independent-replay.txt`](evidence/independent-replay.txt): independently executed
  red/green replay. Temporary paths are replaced with `<TEMP_REPLAY>`.

Raw model transcripts, credentials, runtime databases and host-private paths are not committed.
The displayed plan and result summaries are typed delivery artifacts, not a model transcript.

## Reproduce with your own authenticated Codex CLI

Install AgentBraid and authenticate using the official Codex login flow. You need Git identity
configured and must have the right to sign off the demo commits. This uses provider quota.
From an AgentBraid checkout, with its installed environment active:

```bash
DEMO=$(mktemp -d)
mkdir "$DEMO/workspace"
cp examples/verified-slug/seed/*.py "$DEMO/workspace/"
git -C "$DEMO/workspace" init -b main
git -C "$DEMO/workspace" add slug.py test_slug.py
git -C "$DEMO/workspace" commit --signoff -m 'test: seed whitespace regression demonstration'
python examples/verified-slug/run.py "$DEMO"
```

`run.py` uses the real `CodexAdapter`, service, SQLite state, worktree manager and built-in
final review. It records the actual request, run ID, final snapshot and before/after primary
HEAD in your demo directory. It exits nonzero unless the run completes. It **never applies**.
Model output, plan, timings, IDs, test count and commits can vary; do not expect byte-identical
results or substitute the checked-in evidence for your own run.

The recorded environment had a local Codex config parse error (`features.context_management`
was a table where that CLI expected a boolean). The demonstration used:

```bash
python examples/verified-slug/run.py "$DEMO" --ignore-user-config --model gpt-5.6-sol
```

That explicit, demo-only option subclasses argv construction to pass the CLI's
`--ignore-user-config` flag on both fresh and resumed calls. It does not mock the provider,
disable its sandbox, copy authentication, or edit global configuration. Normal users should
omit it. The recorded run explicitly selected `gpt-5.6-sol`; the portable script uses your
Codex default model unless `--model` is supplied. A clean-wheel replay without an explicit
model failed during planning with `Codex authentication is unavailable`; retrying with the
explicit recorded model failed with the same classification. The clean-wheel end-to-end
replay therefore did not complete. This classification
is not proof that credentials themselves were invalid. Use a model available to your account.
This configuration adaptation means the recorded run is not evidence
that the unmodified default configuration worked on this machine.

## Inspect before any human approval

Inspect `snapshot.json`, confirm `status == "completed"`, read `final_summary`, task results,
notes and validation commands. Inspect the integration diff and independently test it. Use
`python3 -B -m unittest -v` when replaying tests to avoid creating untracked bytecode files.
Check both the primary checkout and integration worktree are clean.

Only after a human approves that specific candidate, call `AgentBraidService.apply_run` with
the recorded run ID and literal `apply-reviewed-run`. Do not infer this approval from permission
to start the run. See [`../../docs/host-walkthrough.md`](../../docs/host-walkthrough.md#7-apply-only-after-separate-approval)
for the equivalent MCP call. Each reproduction needs its **own** human approval; the checked-in
confirmation applies only to the recorded run.

## Failed attempts and boundaries

Two earlier attempts were retained locally, not silently relabeled as successes:

- `2dc9883f2df84fea981cd3b7800326c6`: the request put expected failing RED evidence in
  `validations`. AgentBraid requires all validation entries of a successful mutating task to
  pass and rejected integration. Record the expected RED output in `notes`, and final GREEN
  commands in `validations` instead.
- `96dee7afabc04323ade7a351b373f965`: implementation succeeded, but a separately planned
  review worker could not verify result-metadata placement because ordinary worker prompts
  do not include predecessor results. It honestly reported failure. The final request uses
  AgentBraid's automatic lead review, which receives the full run snapshot, rather than an
  extra review task with inaccessible evidence.

These are real limitations to account for when writing goals. This example verifies the
Codex-only path; it does not claim an authenticated Antigravity-host execution or GUI usability
assessment. The separate maintenance report records MCP transport and Dashboard HTTP checks.
