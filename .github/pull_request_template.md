## Summary

<!-- What changed and why? -->

## Related issue

<!-- Use `Closes #123` when this PR should close an issue. -->

## Validation

- [ ] Tests added or updated where behavior changed
- [ ] `pytest` passes
- [ ] `ruff check .` passes
- [ ] `ruff format --check .` passes
- [ ] `mypy` passes

## Safety and provider policy

- [ ] This change does not read, proxy, or share provider credentials
- [ ] This change does not launch Antigravity as a background worker
- [ ] New command execution is bounded to an explicit workspace
- [ ] Provider-policy implications are documented, or this change has none

## Contribution

- [ ] Every commit includes a DCO `Signed-off-by` line
- [ ] No runtime database, worktree, credential, or model transcript is committed

