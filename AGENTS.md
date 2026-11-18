# Project instructions

This repository contains the Hermes internal bridge plugin (`hermes-internal-bridge`, HIB): a chat relay with a trigger gate, a deterministic route registry, a durable local SQLite bus, context handoff, artifact references, and an audit ledger between isolated Hermes profiles.

## Before changing code

- Read `README.md`, `SECURITY.md`, and `deploy/README.md`.
- Check the current diff and preserve unrelated user changes.
- Keep the spec boundaries intact: no core patches, no `profile_routes`/`multiplex_profiles` edits, no live-route enablement, no credentials copying.
- Python 3.11+, stdlib only: do not add runtime dependencies without a concrete need and owner approval.
- Only use Hermes APIs that exist in v0.23.1 (`requires_hermes: ">=0.23.1"`); imports must stay inside the plugin public API allowlist (`hermes_cli/data/plugin_api.json`).
- Keep user-facing documentation in Russian. Maintain `README.md` as an English, line-for-line structural counterpart to `README-RU.md`; keep source comments/docstrings in English.

## Security and operations

- Treat tokens, API keys, `.env` values, and profile credentials as secrets. Never put them in source, prompts, logs, test fixtures, or reports. The secrets filter (`hermes_internal_bridge/secrets.py`) must run before any ledger or log write.
- The shared bus database holds envelopes and delivery metadata only. Never store memory, credentials, or `.env` contents in it.
- The bus directory must be private: 0700 on Linux, current-user ACL on Windows.
- DLQ and replay are operator-only actions, always ledgered.
- Fail-closed for relay-marked messages even when the gate itself errors; fail-open (with `gate_degraded`) only for ordinary messages.

## Testing

- `uv run pytest` must stay green; add a test for every bug fix and every new error code path.
- Integration tests use fakes (no network, no real platform adapters).
- `hermes plugins validate . --json` and `hermes plugins doctor . --ci` must pass before a release commit.
