# Soleaux LLM usage, subscription limits, and performance telemetry

This document defines the implemented telemetry contracts, supported data sources, local setup, and the remaining provider-adapter work. Provider facts in this file must be revalidated against the linked official documentation before changing an adapter.

## Implemented data path

```text
OpenAI or Anthropic API response
  -> @soleaux/telemetry normalizer
  -> POST /api/v1/usage/events
  -> daemon validation and aggregation
  -> SSE providerUsage + recentUsage
  -> dashboard, MCP, alerts

OpenAI Usage API or Anthropic Admin Usage API
  -> tools/soleaux/telemetry/sync
  -> normalized aggregate UsageEvent records
  -> same daemon, dashboard, and MCP path

Provider subscription usage surface
  -> provider adapter or explicit snapshot import
  -> POST /api/v1/quotas
  -> five-hour/weekly/model/credits reset tracking
  -> quota alerts and dashboard progress
```

## Source-of-truth rules

1. API response usage is exact for the request represented by that response.
2. Organization usage APIs are exact for their documented aggregation dimensions but may arrive later than request instrumentation.
3. Provider subscription progress and reset times are provider-reported snapshots. Do not infer them from API tokens.
4. Forecasts must be stored separately from provider-reported values and marked estimated.
5. Context-window capacity is model/version dependent. It must come from provider metadata or an explicitly versioned model catalog, never a guessed constant.

## Official capability matrix

### OpenAI API

Implemented:

- Request-level input, output, cached-input, reasoning, and total token normalization from Responses or Chat Completions response objects.
- Organization usage synchronization through `GET /v1/organization/usage/completions` using `OPENAI_ADMIN_KEY`.
- Grouping metadata for model, project, API key, user, service tier, and batch.
- Request latency, time to first token, output-token throughput, errors, estimated cost, and context pressure.

Official references:

- Usage API: https://platform.openai.com/docs/api-reference/usage
- Batch/response usage object: https://platform.openai.com/docs/api-reference/batch/object?api-mode=responses
- Models: https://platform.openai.com/docs/models
- Codex with ChatGPT plans: https://help.openai.com/en/articles/11369540/
- Codex rate card: https://help.openai.com/en/articles/20001106-codex-rate-card

Subscription limitation:

Codex plan limits are workload weighted and visible in the Codex Usage panel. The public documentation does not define a stable consumer API that returns the current remaining five-hour or weekly allowance. Soleaux therefore accepts provider-reported snapshots and must not convert API tokens into a supposed Codex subscription percentage.

### Anthropic API and Claude Code

Implemented:

- Request-level input, output, cache-read, and cache-creation token normalization from Messages API response objects.
- Organization usage synchronization through `GET /v1/organizations/usage_report/messages` using `ANTHROPIC_ADMIN_KEY`.
- Grouping metadata for model, workspace, API key, service tier, and context window.
- API-key Claude Code sessions can be metered from JSON output or SDK/API responses and can include session cost.

Official references:

- Messages usage report: https://docs.anthropic.com/en/api/admin-api/usage-cost/get-messages-usage-report
- Claude pricing and usage object: https://docs.anthropic.com/en/docs/about-claude/pricing
- Claude Code CLI JSON output: https://code.claude.com/docs/en/cli-usage
- Models, usage, and limits in Claude Code: https://support.claude.com/en/articles/14552983-models-usage-and-limits-in-claude-code
- Claude Pro five-hour and weekly limits: https://support.claude.com/en/articles/8325606-what-is-the-pro-plan
- Claude Max weekly limits: https://support.claude.com/en/articles/11049741-what-is-the-max-plan
- Claude usage settings and reset display: https://support.claude.com/en/articles/9797557-usage-limit-best-practices

Subscription limitation:

Claude exposes five-hour and weekly progress and reset times in Settings > Usage and Claude Code `/status`. The public documentation does not define a supported consumer usage API for individual Pro or Max accounts. Soleaux stores those values only when supplied by a provider adapter or explicit snapshot capture.

## Local commands

Start the daemon and dashboard:

```bash
pnpm soleaux:telemetry:daemon
pnpm soleaux:telemetry:dashboard
```

Inspect current state:

```bash
pnpm soleaux:telemetry:cli status
```

Synchronize official organization usage APIs:

```bash
export OPENAI_ADMIN_KEY='...'
export ANTHROPIC_ADMIN_KEY='...'
pnpm soleaux:telemetry:sync
```

Sync a time range:

```bash
node tools/soleaux/telemetry/sync/src/sync.mjs openai 2026-07-25T00:00:00Z 2026-07-26T00:00:00Z
node tools/soleaux/telemetry/sync/src/sync.mjs anthropic 2026-07-25T00:00:00Z 2026-07-26T00:00:00Z
```

Record a request produced outside the TypeScript adapter:

```bash
soleaux usage ./usage-event.json
```

Record a provider-reported five-hour or weekly snapshot:

```bash
soleaux quota ./quota-window.json
```

## Usage event schema example

```json
{
  "id": "request-id-or-generated-uuid",
  "providerId": "openai",
  "sessionId": "soleaux-session-id",
  "requestId": "provider-request-id",
  "modelId": "provider-model-id",
  "source": "api-response",
  "occurredAt": 1785070800000,
  "usage": {
    "inputTokens": 12000,
    "cachedInputTokens": 8000,
    "cacheWriteTokens": 0,
    "outputTokens": 1200,
    "reasoningTokens": 400,
    "totalTokens": 13200
  },
  "performance": {
    "requestStartedAt": 1785070790000,
    "firstTokenAt": 1785070791200,
    "completedAt": 1785070800000,
    "latencyMs": 10000,
    "timeToFirstTokenMs": 1200,
    "tokensPerSecond": 136.36,
    "retryCount": 0,
    "status": "completed"
  },
  "contextWindowTokens": 200000,
  "contextTokensUsed": 12000,
  "contextUtilizationPercent": 6,
  "estimatedCostUsd": 0.12
}
```

## Quota window schema example

```json
{
  "id": "anthropic-personal-five-hour",
  "providerId": "anthropic",
  "accountId": "local-account-alias",
  "planId": "pro",
  "label": "Five-hour session",
  "kind": "rolling",
  "metric": "credits",
  "used": 72,
  "limit": 100,
  "remaining": 28,
  "utilizationPercent": 72,
  "resetsAt": 1785088800000,
  "durationSeconds": 18000,
  "source": "provider-status",
  "observedAt": 1785070800000,
  "confidence": 1
}
```

For weekly limits, use a separate quota record. Max plans may require an all-model weekly record and a model-specific weekly record. The IDs must remain stable so a new snapshot replaces the prior snapshot in the daemon.

## TypeScript request instrumentation

```ts
import OpenAI from "openai";
import { SoleauxTelemetryClient } from "@soleaux/telemetry";

const openai = new OpenAI();
const telemetry = new SoleauxTelemetryClient();
const startedAt = Date.now();
const response = await openai.responses.create({
  model: "your-model",
  input: "...",
});
await telemetry.recordOpenAIResponse(response, {
  startedAt,
  completedAt: Date.now(),
});
```

```ts
import Anthropic from "@anthropic-ai/sdk";
import { SoleauxTelemetryClient } from "@soleaux/telemetry";

const anthropic = new Anthropic();
const telemetry = new SoleauxTelemetryClient();
const startedAt = Date.now();
const response = await anthropic.messages.create({
  model: "your-model",
  max_tokens: 1024,
  messages: [{ role: "user", content: "..." }],
});
await telemetry.recordAnthropicMessage(response, {
  startedAt,
  completedAt: Date.now(),
});
```

For streaming, set `firstTokenAt` when the first output-content delta arrives. Do not use the HTTP response-header time as TTFT.

## Required provider adapters still to implement locally

The protocol and ingest surfaces are wired. These adapters require provider credentials or a live local client to validate:

1. Claude Code JSON/stream-JSON adapter for non-interactive runs.
   - Parse documented JSON output.
   - Capture session ID and cost fields only when present.
   - Normalize usage fields from the actual installed client schema.
   - Add fixture tests from sanitized real output.
2. Claude subscription status adapter.
   - Prefer a documented machine-readable interface if Anthropic provides one.
   - Until then, provide a user-authorized capture flow from Settings > Usage or `/status`.
   - Never scrape credentials, cookies, prompts, or conversation content.
3. Codex local event adapter.
   - Validate the installed Codex event/log schema and supported telemetry hooks.
   - Capture provider request IDs, model IDs, token fields, and usage banners where officially emitted.
   - Add fixtures tied to the exact Codex version.
4. Codex subscription usage adapter.
   - Capture the Usage-panel values through an official API if one becomes available.
   - Otherwise use an explicit user-authorized snapshot capture.
5. Model catalog synchronization.
   - Store model ID, provider, context window, deprecation state, and effective dates.
   - Refresh from official provider model documentation or metadata APIs.
   - Never reuse stale context-window values across model aliases.

## Alert behavior implemented

- Quota warning at 80% utilization.
- Critical quota warning at 95% utilization.
- Context warning at 80% utilization.
- Critical context warning at 95% utilization.
- LLM request failure alert.
- Existing CPU process warning.

Thresholds should move into persisted settings before production.

## Persistence required before production

The current daemon uses bounded in-memory stores. Implement SQLite locally and PostgreSQL/TimescaleDB remotely with these tables:

```text
llm_usage_events
quota_windows
provider_accounts
model_catalog
usage_sync_cursors
usage_rollups_hourly
usage_rollups_daily
```

Requirements:

- unique constraint on usage event ID
- upsert quota windows by stable ID
- cursor transaction committed with imported events
- raw-event retention policy
- aggregate retention policy
- account/provider/host/session indexes
- UTC timestamps only
- explicit provenance and confidence columns

## Accuracy requirements

- Reconcile request-level events with organization usage APIs by provider, model, project/workspace, and time bucket.
- Report discrepancies rather than silently overwriting either source.
- Keep cached, cache-write, reasoning, and standard input/output tokens separate.
- Do not include cached tokens twice in `totalTokens` when a provider's documented total already accounts for them.
- Mark imported aggregate records so request count is not confused with one request.
- Store costs as provider-reported when available; otherwise store estimated cost with the exact price-card version used.
- Treat reset times as absolute timestamps supplied by the provider, not `observedAt + 5 hours` unless that is explicitly what the provider reports.
