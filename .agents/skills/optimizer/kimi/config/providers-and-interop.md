# Kimi Providers and Claude Code Interop

Sources verified 2026-07-30:

- https://www.kimi.com/code/docs/en/kimi-code-cli/configuration/providers.html
- https://www.kimi.com/code/docs/en/third-party-tools/claude-code.html
- Kimi Code CLI 0.30.0 binary, for the credential resolution marked below

## Intent

Two adjacent topics: how Kimi reaches a model, and how Claude Code is pointed at Kimi's endpoint instead of Anthropic's. Both live outside this repository, and both can change how work in it behaves.

## Providers

```toml
[providers.<name>]
type = "kimi"
base_url = "https://api.kimi.com/coding/v1"
```

`type` selects the protocol: `kimi` (OpenAI-compatible), `anthropic` (Messages), `openai` (Chat Completions, also DeepSeek and Qwen), `openai_responses` (Responses API), `google-genai` (Gemini direct), `vertexai` (Gemini via Vertex).

Also accepted: `api_key`, `oauth` (populated by login), `env`, `custom_headers`.

### Credentials

Binary-verified resolution, mirroring `resolveProviderApiKey`: **inline `api_key` wins, then the `[providers.<name>.env]` sub-table key, then startup fails.**

```toml
[providers.kimi.env]
KIMI_API_KEY = "the literal key"
```

The sub-table key is the provider's conventional variable _name_, but the value stored beside it is the **literal secret written into the file**. There is no interpolation. `providerValue(configured, env, envKey)` resolves `provider.apiKey` then `provider.env[envKey]` — both config objects — and the docs are explicit that the CLI does not fall back to shell environment variables for credentials. A shell-exported `KIMI_API_KEY` does not reach a `[providers.*]` entry. (`KIMI_API_KEY` is read from the process environment in exactly one place, `resolveFiles`, which builds the Moonshot files client — not provider credential resolution.)

So a **declared** provider always embeds its key. Any `config.toml` carrying one must stay untracked, and the redaction rule in [`config-and-data.md`](config-and-data.md#secrets) is absolute. To keep a key out of every file, do not declare the provider at all — use the environment-synthesized provider below.

### Environment-Synthesized Provider

Binary-verified via `applyEnvModelConfig`; the published configuration pages mention `KIMI_MODEL_*` synthesis only in passing. Setting `KIMI_MODEL_NAME` synthesizes one provider and one model alias **entirely from the environment** and makes it the default model. Nothing is written to disk: the entries exist only in the in-memory runtime config, and two layers enforce that — write paths re-read the raw file, and `writeConfigFile` strips the reserved keys as a guard against patch round-trips.

| Variable | Required | Default |
| --- | --- | --- |
| `KIMI_MODEL_NAME` | yes — the trigger | — |
| `KIMI_MODEL_API_KEY` | yes; startup fails without it | — |
| `KIMI_MODEL_PROVIDER_TYPE` | no | one of `kimi`, `anthropic`, `openai` only |
| `KIMI_MODEL_BASE_URL` | no | `kimi` → `https://api.moonshot.ai/v1`, `openai` → `https://api.openai.com/v1` |
| `KIMI_MODEL_MAX_CONTEXT_SIZE` | no | `262144` |
| `KIMI_MODEL_CAPABILITIES` | no | `image_in`, `thinking` |
| `KIMI_MODEL_MAX_OUTPUT_SIZE`, `KIMI_MODEL_DISPLAY_NAME`, `KIMI_MODEL_REASONING_KEY`, `KIMI_MODEL_ADAPTIVE_THINKING`, `KIMI_MODEL_THINKING_EFFORT` | no | unset |

Three constraints before choosing this path:

- The credential variable is `KIMI_MODEL_API_KEY`. `KIMI_API_KEY` is not a substitute.
- The synthesized alias becomes `default_model` whenever the trigger is set, so this configures one model, not a selection of them.
- The `kimi` default base URL is the Moonshot endpoint, **not** the Kimi Code coding endpoint. Set `KIMI_MODEL_BASE_URL` explicitly when targeting `https://api.kimi.com/coding/v1`.

The synthesized type list is narrower than the declared-provider list: no `openai_responses`, `google-genai`, or `vertexai`.

Conventional variable names by type: `KIMI_API_KEY` / `KIMI_BASE_URL`, `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL`, `OPENAI_API_KEY` / `OPENAI_BASE_URL`, `GOOGLE_API_KEY` with optional `GOOGLE_GEMINI_BASE_URL`, and Application Default Credentials for `vertexai`.

Two provider-specific traps: Vertex requires `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_LOCATION` inside `[providers.vertexai.env]` — shell exports are ignored. For `google-genai`, `base_url` takes the host root only; the SDK appends the API version path.

Prefer `/login` and `/logout` for OAuth-managed accounts over hand-written credentials.

## Models

```toml
[models."k3-1m"]
provider = "kimi"
model = "k3"
max_context_size = 1048576
```

`provider`, `model`, and `max_context_size` are required. Optional: `max_input_size`, `max_output_size`, `capabilities` (`thinking`, `image_in`, `video_in`, `tool_use`, …), `support_efforts`, `default_effort`, `display_name`, `base_url`, and `reasoning_key` to rename the OpenAI reasoning field. Reasoning content from third-party models is handled automatically.

Put local customizations in `[models."<alias>".overrides]` so a provider catalog refresh does not discard them.

## Commands

`/provider` opens the interactive manager; `kimi provider` is the non-interactive equivalent for scripted environments. Known vendors pull a catalog from models.dev; custom registries accept an `api.json` URL with bearer authentication.

## Claude Code Pointed at Kimi

Claude Code can run against Kimi's endpoint. When it does, work in this repository executes on a K3 model, and anything reasoned from Claude model behavior no longer holds.

```bash
export ANTHROPIC_BASE_URL=https://api.kimi.com/coding/
export ANTHROPIC_MODEL="k3-256k"
export CLAUDE_CODE_EFFORT_LEVEL=high
export CLAUDE_CODE_MAX_CONTEXT_TOKENS=262144
```

`ANTHROPIC_API_KEY` holds a Kimi Code console key rather than an Anthropic one.

- **`k3[1m]` is valid only in Claude Code environment variables.** Everywhere else — `config.toml`, provider aliases, `/model` — the identifier is `k3` without brackets.
- Model availability follows the membership tier: `kimi-for-coding` at 262K, adding `k3` and `k3-256k` higher up, with a 1M-context tier above that.
- Effort maps `medium` and `high` to K3 `high`, and `xhigh` and `max` to K3 `max`. `/effort` toggles it in session.
- Verify with `/status`; a correct setup reports the Kimi base URL.

**The published setup script rewrites `~/.claude.json` and `~/.claude/settings.json`.** Those are machine-level Claude Code files outside this repository, and the rewrite disables the default Anthropic login path. Never run it implicitly, as part of another task, or on a machine whose Claude Code configuration you did not author — set the environment variables by hand instead.
