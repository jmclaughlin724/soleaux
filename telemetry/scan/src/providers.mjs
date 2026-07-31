import { createHash } from "node:crypto";
import { normalizeEvent } from "./analyzer.mjs";

const ZERO = 0;
const STABLE_ID_HEX_LENGTH = 24;
const PROVIDER_SAMPLE_LIMIT = 20;

const number = (value) =>
  Number.isFinite(Number(value)) ? Number(value) : ZERO;
const stableId = (value) =>
  createHash("sha256")
    .update(JSON.stringify(value))
    .digest("hex")
    .slice(ZERO, STABLE_ID_HEX_LENGTH);
const recordsOf = (records) => (Array.isArray(records) ? records : [records]);

// Use the record's own timestamp when one exists; otherwise preserve ordering
// with a synthetic value and say so in metadata instead of silently faking time.
const recordTime = (record, fallback) => {
  const raw = record?.timestamp ?? record?.ts ?? record?.created_at;
  const parsed = typeof raw === "number" ? raw : Date.parse(raw ?? "");
  return Number.isFinite(parsed)
    ? { at: parsed, source: "record" }
    : { at: fallback, source: "synthetic-order-preserving" };
};

// Canonical source: OpenAI Codex SDK and `codex exec --json` expose structured
// JSONL events. Turn-level usage is emitted on `turn.completed`; item events do
// not carry provider token attribution. Do not allocate turn tokens to tools.
export function parseCodexTranscript(records, source = "codex-jsonl") {
  const events = [];
  for (const [index, record] of recordsOf(records).entries()) {
    if (!record || typeof record !== "object") {
      continue;
    }
    if (
      record.type === "turn.completed" &&
      record.usage &&
      typeof record.usage === "object"
    ) {
      const inputTokens = number(record.usage.input_tokens);
      const cachedInputTokens = number(record.usage.cached_input_tokens);
      const outputTokens = number(record.usage.output_tokens);
      const reasoningTokens = number(record.usage.reasoning_output_tokens);
      const time = recordTime(record, Date.now() + index);
      events.push(
        normalizeEvent(
          {
            id: `codex-turn-${stableId([source, index, record])}`,
            providerId: "openai",
            sessionId: record.thread_id,
            modelId: record.model ?? "unknown",
            occurredAt: time.at,
            toolCategory: "model",
            toolName: "codex-turn",
            usage: {
              inputTokens,
              cachedInputTokens,
              outputTokens,
              reasoningTokens,
              totalTokens:
                number(record.usage.total_tokens) || inputTokens + outputTokens,
            },
            metadata: {
              attribution: "provider-reported-turn-usage",
              schema: "codex-exec-jsonl",
              transcriptSource: source,
              timestampSource: time.source,
            },
          },
          source
        )
      );
      continue;
    }

    if (
      (record.type === "item.started" || record.type === "item.completed") &&
      record.item &&
      typeof record.item === "object"
    ) {
      const { item } = record;
      const time = recordTime(record, Date.now() + index);
      events.push(
        normalizeEvent(
          {
            id: `codex-item-${stableId([source, index, item.id, record.type])}`,
            providerId: "openai",
            sessionId: record.thread_id,
            occurredAt: time.at,
            toolName: String(item.type ?? "item"),
            command: typeof item.command === "string" ? item.command : "",
            output:
              typeof item.aggregated_output === "string"
                ? item.aggregated_output
                : "",
            status:
              item.status ??
              (record.type === "item.completed" ? "completed" : "running"),
            usage: {
              inputTokens: 0,
              cachedInputTokens: 0,
              outputTokens: 0,
              reasoningTokens: 0,
              totalTokens: 0,
            },
            metadata: {
              attribution: "tool-event-without-provider-token-usage",
              schema: "codex-exec-jsonl",
              transcriptSource: source,
              timestampSource: time.source,
            },
          },
          source
        )
      );
    }
  }
  return events;
}

// Claude Code documents JSON and stream-JSON output, but the CLI reference does
// not define a stable tool-level token attribution schema. Only documented final
// result metadata is imported here. Anthropic API usage objects should enter via
// the API normalizer, not be inferred from arbitrary transcript shapes.
export function parseClaudeTranscript(records, source = "anthropic-json") {
  const events = [];
  for (const [index, record] of recordsOf(records).entries()) {
    if (!record || typeof record !== "object" || record.type !== "result") {
      continue;
    }
    const time = recordTime(record, Date.now() + index);
    events.push(
      normalizeEvent(
        {
          id: `anthropic-result-${stableId([source, index, record.session_id, record])}`,
          providerId: "anthropic",
          sessionId: record.session_id,
          occurredAt: time.at,
          toolCategory: "model",
          toolName: "anthropic-result",
          status: record.is_error ? "failed" : "completed",
          latencyMs: number(record.duration_ms),
          estimatedCostUsd: number(record.total_cost_usd),
          usage: {
            inputTokens: 0,
            cachedInputTokens: 0,
            cacheWriteTokens: 0,
            outputTokens: 0,
            reasoningTokens: 0,
            totalTokens: 0,
          },
          metadata: {
            attribution: "documented-result-metadata-without-token-usage",
            schema: "anthropic-claude-code-json-result",
            transcriptSource: source,
            timestampSource: time.source,
            numTurns: number(record.num_turns),
            durationApiMs: number(record.duration_api_ms),
          },
        },
        source
      )
    );
  }
  return events;
}

export function detectProvider(records) {
  const sample = recordsOf(records).slice(ZERO, PROVIDER_SAMPLE_LIMIT);
  if (
    sample.some(
      (record) =>
        record?.type === "turn.completed" ||
        record?.type === "item.completed" ||
        record?.type === "thread.started"
    )
  ) {
    return "codex";
  }
  if (
    sample.some(
      (record) =>
        record?.type === "result" &&
        ("session_id" in record || "total_cost_usd" in record)
    )
  ) {
    return "claude";
  }
  return "generic";
}
