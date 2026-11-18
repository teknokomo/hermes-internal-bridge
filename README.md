🇬🇧 **English** | 🇷🇺 [Русский](README-RU.md)

# Hermes Internal Bridge (HIB) 0.1.0

An internal bridge between Hermes profiles: a personal chat relay with a trigger gate, deterministic route policy, a durable local message bus, context handoff, artifact references, and an audit ledger.

HIB is an umbrella Hermes plugin. It does not replace Hermes core, does not build a second A2A, and does not replace Kanban. Its role: **policy, routing, envelope, and audit** on top of native mechanisms.

## Modules (0.1.0)

| Module | Purpose |
|---|---|
| `chat-relay` | personal Telegram/MAX chat → profile topic/chat relay |
| `directory` | registry of local profiles, roles, capabilities, health |
| `profile-bus` | durable messages and tasks between profiles |
| `ledger` | audit, correlation, retry, dead-letter |
| `policy` | routes, ACL, limits |
| `cli` | `hib status/routes/relay/replay/peers/directory` |
| `approvals` | reserved interface (implementation in 0.2.0) |
| `federation` | reserved interface (implementation in 0.3.0) |

## Principles

- One route — one destination. No fan-out.
- No native forward. Only generated messages with relay metadata.
- A mention is not a trigger for relay/forwarded messages.
- One hop. A relay message cannot be forwarded further.
- Policy first, LLM second. The route is chosen by configuration.
- Secrets are never relayed.
- Profiles stay isolated. Summaries, references, and artifact links are passed — never memory or credentials.
- Fail-closed on uncertainty. No route — rejection, not "the nearest profile".

## Installation (operator)

See [deploy/README.md](deploy/README.md) for the operator runbook. Short version:

1. Install Hermes Agent 0.23.1+ (`requires_hermes: ">=0.23.1"`).
2. Install the plugin into the target profiles with `hermes plugins install teknokomo/hermes-internal-bridge --no-enable` (or adopt a self-cloned directory).
3. Set `plugins.entries.hermes-internal-bridge.allow_gateway_injection: true` in every target profile.
4. Create routes with the `hib routes` CLI. Routes are enabled by the operator only.
5. Run manual acceptance (`deploy/README.md`, final section) with real chats.

## Development

```bash
uv venv
uv pip install pytest ruff
uv run pytest
hermes plugins validate . --json
hermes plugins doctor . --ci
```

Python 3.11+, no runtime dependencies (stdlib only). Linux is the priority platform; Windows is supported for development.

## License

Distributed under the Omsk Open License — Base Modification. Attribution. The full English text is in [LICENSE.md](LICENSE.md), the Russian source text is in [LICENSE-RU.md](LICENSE-RU.md).
