import assert from "node:assert/strict";
import test from "node:test";
import {
  detectProvider,
  parseClaudeTranscript,
  parseCodexTranscript,
} from "../src/providers.mjs";

test("codex turn events use record timestamps when present", () => {
  const records = [
    {
      type: "turn.completed",
      thread_id: "thread-1",
      timestamp: 1_753_000_000_000,
      usage: {
        input_tokens: 100,
        cached_input_tokens: 10,
        output_tokens: 20,
        reasoning_output_tokens: 5,
      },
    },
  ];
  const [event] = parseCodexTranscript(records);
  const expectedRecordTimestampUnixMs = 1_753_000_000_000;
  const expectedTotalTokens = 120;
  const expectedCachedInputTokens = 10;
  assert.equal(event.startedAt, expectedRecordTimestampUnixMs);
  assert.equal(event.metadata.timestampSource, "record");
  assert.equal(event.totalTokens, expectedTotalTokens);
  assert.equal(event.cachedInputTokens, expectedCachedInputTokens);
});

test("codex events without timestamps are marked synthetic", () => {
  const records = [
    {
      type: "item.completed",
      thread_id: "thread-1",
      item: { id: "item-1", type: "command_execution", command: "rg TODO ." },
    },
  ];
  const [event] = parseCodexTranscript(records);
  assert.equal(event.metadata.timestampSource, "synthetic-order-preserving");
  assert.equal(
    event.metadata.attribution,
    "tool-event-without-provider-token-usage"
  );
  const expectedToolEventTotalTokens = 0;
  assert.equal(event.totalTokens, expectedToolEventTotalTokens);
});

test("claude result events import documented metadata only", () => {
  const records = [
    {
      type: "result",
      session_id: "session-1",
      is_error: false,
      duration_ms: 1200,
      duration_api_ms: 900,
      num_turns: 3,
      total_cost_usd: 0.01,
    },
  ];
  assert.equal(detectProvider(records), "claude");
  const [event] = parseClaudeTranscript(records);
  const expectedLatencyMs = 1200;
  const expectedEstimatedCostUsd = 0.01;
  const expectedTotalTokens = 0;
  assert.equal(event.providerId, "anthropic");
  assert.equal(event.latencyMs, expectedLatencyMs);
  assert.equal(event.estimatedCostUsd, expectedEstimatedCostUsd);
  assert.equal(event.totalTokens, expectedTotalTokens);
  assert.equal(event.metadata.timestampSource, "synthetic-order-preserving");
});

test("detectProvider distinguishes codex, claude, and generic records", () => {
  assert.equal(detectProvider([{ type: "thread.started" }]), "codex");
  assert.equal(detectProvider([{ type: "result", session_id: "s" }]), "claude");
  assert.equal(detectProvider([{ anything: true }]), "generic");
});
