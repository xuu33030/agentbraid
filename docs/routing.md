# Routing policy

AgentBraid v0.1 routes every planned task with a deterministic score. The same plan, capability
snapshots, and timestamp produce the same assignments. Ties select Codex because Codex is the
accountable lead.

The score records these components in each task's assignment rationale:

- task-kind fit for implementation, testing, review, research, documentation, and integration
- the lead's preferred executor, treated as a preference rather than an unconditional command
- task risk and whether the task mutates the workspace
- provider health, active cooldowns, and availability
- smoothed historical success rate and average observed latency
- required capabilities, including the explicit `executor:codex` and `executor:host` constraints

Unavailable providers and providers in an active cooldown are not selected. If every executor is
unavailable, routing fails instead of silently assigning work that cannot run.

The v0.1 weights are fixed in source and covered by tests. Adaptive weight learning is deferred
until after v0.1 so early behavior remains inspectable and reproducible.
