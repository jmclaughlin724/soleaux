#!/usr/bin/env node
import { createHash } from "node:crypto";
import { pathToFileURL } from "node:url";
import { resolveDaemonOrigin } from "@soleaux/protocol/env";

const HTTP_CONFLICT = 409;
const MS_PER_SECOND = 1000;
const SECONDS_PER_MINUTE = 60;
const MINUTES_PER_HOUR = 60;
const HOURS_PER_DAY = 24;
const MS_PER_DAY =
  HOURS_PER_DAY * MINUTES_PER_HOUR * SECONDS_PER_MINUTE * MS_PER_SECOND;
const NUMBER_FALLBACK = 0;
const UNIT_SCALE = 1;
const THOUSAND_SCALE = 1000;
const MILLION_SCALE = 1_000_000;
const SCALE_BY_SUFFIX = { m: MILLION_SCALE, k: THOUSAND_SCALE };
const CONTEXT_WINDOW_PATTERN = /(?<digits>[0-9.]+)\s*(?<suffix>[km])?/iu;
const FAILURE_EXIT_CODE = 1;
const ENTRYPOINT_ARGUMENT_INDEX = 1;
const CLI_ARGUMENT_OFFSET = 2;

// Daemon base URL is the bare origin; the API prefix is appended here.
const daemonUrl = `${resolveDaemonOrigin(process.env.SOLEAUX_DAEMON_URL)}/api/v1`;

function number(value) {
  const parsed = Number(value ?? NUMBER_FALLBACK);
  return Number.isFinite(parsed) ? parsed : NUMBER_FALLBACK;
}

function stableId(...parts) {
  return createHash("sha256")
    .update(parts.map((value) => String(value ?? "")).join("|"))
    .digest("hex");
}

async function recordUsage(event) {
  const response = await fetch(`${daemonUrl}/usage/events`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(event),
  });
  if (response.status === HTTP_CONFLICT) {
    return;
  }
  if (!response.ok) {
    throw new Error(
      `Soleaux ingest failed: ${response.status} ${await response.text()}`
    );
  }
}

function parseContextWindow(value) {
  if (typeof value === "number") {
    return value;
  }
  const match = CONTEXT_WINDOW_PATTERN.exec(String(value ?? ""));
  const groups = match?.groups;
  const suffix = groups?.suffix?.toLowerCase();
  const scale = (suffix ? SCALE_BY_SUFFIX[suffix] : undefined) ?? UNIT_SCALE;
  return groups ? Number(groups.digits) * scale : undefined;
}

function openAIEvents(payload) {
  const events = [];
  const buckets = payload.data ?? [];
  for (const bucket of buckets) {
    const results = bucket.results ?? [];
    for (const result of results) {
      const input = number(result.input_tokens);
      const output = number(result.output_tokens);
      const cached = number(result.input_cached_tokens);
      events.push({
        id: stableId(
          "openai",
          bucket.start_time,
          result.project_id,
          result.api_key_id,
          result.model
        ),
        providerId: "openai",
        accountId: result.api_key_id ?? undefined,
        workspaceId: result.project_id ?? undefined,
        modelId: result.model ?? "unattributed",
        source: "import",
        occurredAt:
          Number(bucket.end_time ?? bucket.start_time) * MS_PER_SECOND,
        usage: {
          inputTokens: input,
          cachedInputTokens: cached,
          cacheWriteTokens: 0,
          outputTokens: output,
          reasoningTokens: 0,
          totalTokens: input + output,
        },
        performance: {
          requestStartedAt: Number(bucket.start_time) * MS_PER_SECOND,
          completedAt: Number(bucket.end_time) * MS_PER_SECOND,
          retryCount: 0,
          status: "completed",
        },
        metadata: {
          aggregate: true,
          requestCount: number(result.num_model_requests),
          userId: result.user_id ?? null,
          batch: result.batch ?? null,
        },
      });
    }
  }
  return events;
}

function anthropicEvents(payload) {
  const events = [];
  const buckets = payload.data ?? [];
  for (const bucket of buckets) {
    const results = bucket.results ?? [];
    for (const result of results) {
      const input = number(result.uncached_input_tokens);
      const cached = number(result.cache_read_input_tokens);
      const cacheWrite =
        number(result.cache_creation?.ephemeral_5m_input_tokens) +
        number(result.cache_creation?.ephemeral_1h_input_tokens);
      const output = number(result.output_tokens);
      const occurredDate = new Date(bucket.ending_at ?? bucket.starting_at);
      const startedDate = new Date(bucket.starting_at);
      const endedDate = new Date(bucket.ending_at);
      events.push({
        id: stableId(
          "anthropic",
          bucket.starting_at,
          result.api_key_id,
          result.workspace_id,
          result.model,
          result.service_tier,
          result.context_window
        ),
        providerId: "anthropic",
        accountId: result.api_key_id ?? undefined,
        workspaceId: result.workspace_id ?? undefined,
        modelId: result.model ?? "unattributed",
        source: "import",
        occurredAt: occurredDate.valueOf(),
        usage: {
          inputTokens: input,
          cachedInputTokens: cached,
          cacheWriteTokens: cacheWrite,
          outputTokens: output,
          reasoningTokens: 0,
          totalTokens: input + cached + cacheWrite + output,
        },
        performance: {
          requestStartedAt: startedDate.valueOf(),
          completedAt: endedDate.valueOf(),
          retryCount: 0,
          status: "completed",
        },
        contextWindowTokens: parseContextWindow(result.context_window),
        metadata: {
          aggregate: true,
          requestCount: number(result.request_count),
          serviceTier: result.service_tier ?? null,
          contextWindow: result.context_window ?? null,
        },
      });
    }
  }
  return events;
}

async function fetchOpenAIPage(key, start, end, page) {
  const url = new URL(
    "https://api.openai.com/v1/organization/usage/completions"
  );
  url.searchParams.set(
    "start_time",
    String(Math.floor(start.valueOf() / MS_PER_SECOND))
  );
  url.searchParams.set(
    "end_time",
    String(Math.floor(end.valueOf() / MS_PER_SECOND))
  );
  url.searchParams.set("bucket_width", "1h");
  url.searchParams.set("limit", "168");
  // group_by accepts the documented fields only (repeated params, matching
  // the cookbook's wire form); service_tier is not a supported group_by value.
  for (const field of ["model", "project_id", "api_key_id", "user_id"]) {
    url.searchParams.append("group_by", field);
  }
  if (page) {
    url.searchParams.set("page", page);
  }

  const response = await fetch(url, {
    headers: { authorization: `Bearer ${key}` },
  });
  if (!response.ok) {
    throw new Error(
      `OpenAI usage sync failed: ${response.status} ${await response.text()}`
    );
  }
  const payload = await response.json();
  await Promise.all(openAIEvents(payload).map((event) => recordUsage(event)));
  return payload.has_more ? payload.next_page : undefined;
}

async function syncOpenAI(provider, start, end) {
  const key = process.env.OPENAI_ADMIN_KEY;
  if (!key) {
    if (provider === "openai") {
      throw new Error("OPENAI_ADMIN_KEY is required");
    }
    console.warn("Skipping OpenAI: OPENAI_ADMIN_KEY is not set");
    return;
  }

  let page;
  do {
    // eslint-disable-next-line no-await-in-loop -- pagination depends on the previous page
    page = await fetchOpenAIPage(key, start, end, page);
  } while (page);
}

async function fetchAnthropicPage(key, start, end, page) {
  const url = new URL(
    "https://api.anthropic.com/v1/organizations/usage_report/messages"
  );
  url.searchParams.set("starting_at", start.toISOString());
  url.searchParams.set("ending_at", end.toISOString());
  url.searchParams.set("bucket_width", "1h");
  url.searchParams.set("limit", "168");
  for (const field of [
    "api_key_id",
    "workspace_id",
    "model",
    "service_tier",
    "context_window",
  ]) {
    url.searchParams.append("group_by[]", field);
  }
  if (page) {
    url.searchParams.set("page", page);
  }

  const response = await fetch(url, {
    headers: {
      "x-api-key": key,
      "anthropic-version": "2023-06-01",
      accept: "application/json",
    },
  });
  if (!response.ok) {
    throw new Error(
      `Anthropic usage sync failed: ${response.status} ${await response.text()}`
    );
  }
  const payload = await response.json();
  await Promise.all(
    anthropicEvents(payload).map((event) => recordUsage(event))
  );
  return payload.has_more ? payload.next_page : undefined;
}

async function syncAnthropic(provider, start, end) {
  const key = process.env.ANTHROPIC_ADMIN_KEY;
  if (!key) {
    if (provider === "anthropic") {
      throw new Error("ANTHROPIC_ADMIN_KEY is required");
    }
    console.warn("Skipping Anthropic: ANTHROPIC_ADMIN_KEY is not set");
    return;
  }

  let page;
  do {
    // eslint-disable-next-line no-await-in-loop -- pagination depends on the previous page
    page = await fetchAnthropicPage(key, start, end, page);
  } while (page);
}

async function main() {
  const [provider = "all", startArgument, endArgument] =
    process.argv.slice(CLI_ARGUMENT_OFFSET);
  const start = startArgument
    ? new Date(startArgument)
    : new Date(Date.now() - MS_PER_DAY);
  const end = endArgument ? new Date(endArgument) : new Date();

  if (Number.isNaN(start.valueOf()) || Number.isNaN(end.valueOf())) {
    throw new TypeError(
      "Dates must be ISO-8601 values: soleaux-provider-sync [openai|anthropic|all] [start] [end]"
    );
  }

  if (provider === "openai" || provider === "all") {
    await syncOpenAI(provider, start, end);
  }
  if (provider === "anthropic" || provider === "all") {
    await syncAnthropic(provider, start, end);
  }
}

const runCli = async () => {
  try {
    await main();
  } catch (error) {
    const message = Error.isError(error)
      ? error.message
      : "unknown provider-sync failure";
    process.stderr.write(`${message}\n`);
    process.exitCode = FAILURE_EXIT_CODE;
  }
};

if (
  process.argv[ENTRYPOINT_ARGUMENT_INDEX] !== undefined &&
  import.meta.url ===
    pathToFileURL(process.argv[ENTRYPOINT_ARGUMENT_INDEX]).href
) {
  void runCli();
}
