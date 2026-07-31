import { randomUUID } from "node:crypto";
import { resolveDaemonOrigin } from "@soleaux/protocol/env";
import type { QuotaWindow, UsageEvent } from "@soleaux/protocol";

export type TelemetryMetadataValue = string | number | boolean | null;
export type TelemetryMetadata = Record<string, TelemetryMetadataValue>;

export interface TelemetryClientOptions {
  daemonUrl?: string;
  sessionId?: string;
  accountId?: string;
  workspaceId?: string;
}

export interface RequestTiming {
  startedAt: number;
  firstTokenAt?: number;
  completedAt?: number;
  retryCount?: number;
}

export interface OpenAIResponseLike {
  id?: string;
  model?: string;
  usage?: {
    input_tokens?: number;
    output_tokens?: number;
    total_tokens?: number;
    input_tokens_details?: { cached_tokens?: number };
    output_tokens_details?: { reasoning_tokens?: number };
    prompt_tokens?: number;
    completion_tokens?: number;
    prompt_tokens_details?: { cached_tokens?: number };
    completion_tokens_details?: { reasoning_tokens?: number };
  };
  error?: { code?: string };
}

export interface AnthropicMessageLike {
  id?: string;
  model?: string;
  usage?: {
    input_tokens?: number;
    output_tokens?: number;
    cache_creation_input_tokens?: number;
    cache_read_input_tokens?: number;
  };
  error?: { type?: string };
}

interface NormalizeDefaults {
  sessionId?: string;
  accountId?: string;
  workspaceId?: string;
  metadata?: TelemetryMetadata;
  contextWindowTokens?: number;
  contextTokensUsed?: number;
  estimatedCostUsd?: number;
  creditsConsumed?: number;
}

interface PostResult {
  status: number;
  data: unknown;
}

async function postJson(url: string, body: unknown): Promise<PostResult> {
  const response = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const text = await response.text();
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    data = text;
  }
  return { status: response.status, data };
}

function failureMessage(status: number, data: unknown): string {
  const body = typeof data === "string" ? data : JSON.stringify(data);
  return `Soleaux telemetry request failed: ${status} ${body}`;
}

function isUsageEvent(data: unknown): data is UsageEvent {
  if (typeof data !== "object" || data === null) {
    return false;
  }
  if (!("id" in data) || typeof data.id !== "string") {
    return false;
  }
  return (
    "usage" in data && typeof data.usage === "object" && data.usage !== null
  );
}

function isQuotaWindow(data: unknown): data is QuotaWindow {
  if (typeof data !== "object" || data === null) {
    return false;
  }
  if (!("id" in data) || typeof data.id !== "string") {
    return false;
  }
  return "used" in data && typeof data.used === "number";
}

function buildEvent(input: {
  id?: string;
  providerId: string;
  modelId: string;
  source: UsageEvent["source"];
  requestId?: string;
  inputTokens: number;
  outputTokens: number;
  cachedInputTokens: number;
  cacheWriteTokens: number;
  reasoningTokens: number;
  totalTokens: number;
  errorCode?: string;
  timing: RequestTiming;
  defaults: NormalizeDefaults;
}): UsageEvent {
  const completedAt = input.timing.completedAt ?? Date.now();
  const latencyMs = Math.max(0, completedAt - input.timing.startedAt);
  const { firstTokenAt } = input.timing;
  const timeToFirstTokenMs =
    firstTokenAt === undefined
      ? undefined
      : Math.max(0, firstTokenAt - input.timing.startedAt);
  const generationSeconds = Math.max(
    0.001,
    (completedAt - (firstTokenAt ?? input.timing.startedAt)) / 1000
  );
  const contextWindowTokens = input.defaults.contextWindowTokens ?? 0;
  const contextTokensUsed = input.defaults.contextTokensUsed ?? 0;
  const contextUtilizationPercent =
    contextWindowTokens > 0 && contextTokensUsed > 0
      ? (contextTokensUsed / contextWindowTokens) * 100
      : undefined;

  return {
    id: input.id ?? randomUUID(),
    providerId: input.providerId,
    accountId: input.defaults.accountId,
    workspaceId: input.defaults.workspaceId,
    sessionId: input.defaults.sessionId,
    requestId: input.requestId,
    modelId: input.modelId,
    source: input.source,
    occurredAt: completedAt,
    usage: {
      inputTokens: input.inputTokens,
      cachedInputTokens: input.cachedInputTokens,
      cacheWriteTokens: input.cacheWriteTokens,
      outputTokens: input.outputTokens,
      reasoningTokens: input.reasoningTokens,
      totalTokens: input.totalTokens,
    },
    performance: {
      requestStartedAt: input.timing.startedAt,
      firstTokenAt,
      completedAt,
      latencyMs,
      timeToFirstTokenMs,
      tokensPerSecond: input.outputTokens / generationSeconds,
      retryCount: input.timing.retryCount ?? 0,
      status:
        input.errorCode !== undefined && input.errorCode !== ""
          ? "failed"
          : "completed",
      errorCode: input.errorCode,
    },
    contextWindowTokens: input.defaults.contextWindowTokens,
    contextTokensUsed: input.defaults.contextTokensUsed,
    contextUtilizationPercent,
    estimatedCostUsd: input.defaults.estimatedCostUsd,
    creditsConsumed: input.defaults.creditsConsumed,
    metadata: input.defaults.metadata,
  };
}

export function normalizeOpenAIResponse(
  response: OpenAIResponseLike,
  timing: RequestTiming,
  defaults: NormalizeDefaults = {}
): UsageEvent {
  const usage = response.usage ?? {};
  const inputTokens = usage.input_tokens ?? usage.prompt_tokens ?? 0;
  const outputTokens = usage.output_tokens ?? usage.completion_tokens ?? 0;
  const cachedInputTokens =
    usage.input_tokens_details?.cached_tokens ??
    usage.prompt_tokens_details?.cached_tokens ??
    0;
  const reasoningTokens =
    usage.output_tokens_details?.reasoning_tokens ??
    usage.completion_tokens_details?.reasoning_tokens ??
    0;
  return buildEvent({
    id: response.id,
    providerId: "openai",
    modelId: response.model ?? "unknown",
    source: "api-response",
    requestId: response.id,
    inputTokens,
    outputTokens,
    cachedInputTokens,
    cacheWriteTokens: 0,
    reasoningTokens,
    totalTokens: usage.total_tokens ?? inputTokens + outputTokens,
    errorCode: response.error?.code,
    timing,
    defaults,
  });
}

export function normalizeAnthropicMessage(
  response: AnthropicMessageLike,
  timing: RequestTiming,
  defaults: NormalizeDefaults = {}
): UsageEvent {
  const usage = response.usage ?? {};
  const inputTokens = usage.input_tokens ?? 0;
  const outputTokens = usage.output_tokens ?? 0;
  const cachedInputTokens = usage.cache_read_input_tokens ?? 0;
  const cacheWriteTokens = usage.cache_creation_input_tokens ?? 0;
  return buildEvent({
    id: response.id,
    providerId: "anthropic",
    modelId: response.model ?? "unknown",
    source: "api-response",
    requestId: response.id,
    inputTokens,
    outputTokens,
    cachedInputTokens,
    cacheWriteTokens,
    reasoningTokens: 0,
    totalTokens:
      inputTokens + cachedInputTokens + cacheWriteTokens + outputTokens,
    errorCode: response.error?.type,
    timing,
    defaults,
  });
}

export class SoleauxTelemetryClient {
  private readonly daemonUrl: string;
  private readonly defaults: Pick<
    UsageEvent,
    "sessionId" | "accountId" | "workspaceId"
  >;

  constructor(options: TelemetryClientOptions = {}) {
    this.daemonUrl = resolveDaemonOrigin(
      options.daemonUrl ?? process.env.SOLEAUX_DAEMON_URL
    );
    this.defaults = {
      sessionId: options.sessionId ?? process.env.SOLEAUX_SESSION_ID,
      accountId: options.accountId,
      workspaceId: options.workspaceId,
    };
  }

  async recordUsage(event: UsageEvent): Promise<UsageEvent> {
    const merged = { ...this.defaults, ...event };
    const { status, data } = await postJson(
      `${this.daemonUrl}/api/v1/usage/events`,
      merged
    );
    if (status === 201) {
      return isUsageEvent(data) ? data : merged;
    }
    if (status === 409 && isUsageEvent(data)) {
      return data;
    }
    throw new Error(failureMessage(status, data));
  }

  async recordQuota(quota: QuotaWindow): Promise<QuotaWindow> {
    const { status, data } = await postJson(
      `${this.daemonUrl}/api/v1/quotas`,
      quota
    );
    if (status === 201) {
      return isQuotaWindow(data) ? data : quota;
    }
    if (status === 409 && isQuotaWindow(data)) {
      return data;
    }
    throw new Error(failureMessage(status, data));
  }

  async recordOpenAIResponse(
    input: OpenAIResponseLike,
    timing: RequestTiming,
    metadata: TelemetryMetadata = {}
  ) {
    return await this.recordUsage(
      normalizeOpenAIResponse(input, timing, { ...this.defaults, metadata })
    );
  }

  async recordAnthropicMessage(
    input: AnthropicMessageLike,
    timing: RequestTiming,
    metadata: TelemetryMetadata = {}
  ) {
    return await this.recordUsage(
      normalizeAnthropicMessage(input, timing, { ...this.defaults, metadata })
    );
  }
}
