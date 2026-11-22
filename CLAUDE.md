# Instructions for agents working in this repository

## Scope

- 0.1.0 scope: chat relay, trigger gate, route registry, directory, local bus, context handoff, artifact references, ledger, CLI, plugin tools. Approvals and federation are reserved interfaces only — their envelope types are validated and rejected (`approval_not_supported`, `federation_not_supported`).
- Do not touch: `hermes-multica-bridge`, core Hermes, `gateway.profile_routes`, `gateway.multiplex_profiles`, other plugins' configuration or state, live profiles.
- Do not read: `.env`, `auth.json`, `state.db`, `MEMORY.md`, `USER.md`, `SOUL.md` of any profile.

## Working rules

- Follow the decisions in `docs/implementation-plan-0.1.0.md` (variant B delivery, shared bus on the Hermes root home, source-profile transcription, mirror via the bus).
- Stop and ask the owner instead of guessing when: a requirement diverges from live Hermes code, a secret or access is needed, data loss is possible, or publication/release decisions are required.
- Every new behavior gets a test; §10 error codes are asserted literally in tests.
- Commits are made in the repository owner's name; do not mention agents in commits, docs, or code comments.
