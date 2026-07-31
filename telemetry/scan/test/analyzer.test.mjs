import assert from "node:assert/strict";
import test from "node:test";
import { analyzeScan, normalizeEvent } from "../src/analyzer.mjs";

const base = {
  providerId: "openai",
  sessionId: "session-1",
  modelId: "gpt-test",
  toolName: "bash",
  command: "rg TODO .",
  usage: {
    inputTokens: 1000,
    cachedInputTokens: 200,
    outputTokens: 100,
    reasoningTokens: 50,
    totalTokens: 1100,
  },
  latencyMs: 2000,
  cpuPercent: 50,
  residentMemoryBytes: 100_000_000,
  status: "completed",
};

test("reports exact repeats as candidates requiring matched evidence", () => {
  const first = normalizeEvent({ ...base, id: "one", occurredAt: 1000 });
  const second = normalizeEvent({ ...base, id: "two", occurredAt: 2000 });
  const report = analyzeScan({ events: [first, second] });
  const expectedCombinedTotalTokens = 2200;
  assert.equal(report.totals.totalTokens, expectedCombinedTotalTokens);
  assert.ok(
    report.observations.some(
      (observation) => observation.type === "exact-repeat"
    )
  );
  assert.equal(
    report.definitiveResultRequirements.wastedTokens.status,
    "requires-evidence"
  );
  assert.ok(
    report.definitiveResultRequirements.wastedTokens.missing.includes(
      "taskId and taskVersion"
    )
  );
});

test("does not double-count cached or reasoning subcategories", () => {
  const event = normalizeEvent({
    ...base,
    id: "derived-total",
    usage: {
      inputTokens: 1000,
      cachedInputTokens: 800,
      outputTokens: 100,
      reasoningTokens: 90,
    },
    occurredAt: 1000,
  });
  const expectedDerivedTotalTokens = 1100;
  assert.equal(event.totalTokens, expectedDerivedTotalTokens);
});

test("reports the instrumentation required for definitive savings", () => {
  const event = normalizeEvent({
    ...base,
    id: "failed",
    status: "failed",
    occurredAt: 1000,
  });
  const report = analyzeScan({
    events: [event],
    quotas: [{ label: "Provider snapshot", used: 50 }],
  });
  assert.equal(
    report.definitiveResultRequirements.subscriptionCapacity.status,
    "requires-evidence"
  );
  assert.equal(
    report.definitiveResultRequirements.soleauxSavings.status,
    "requires-evidence"
  );
  assert.ok(
    report.definitiveResultRequirements.cpuAndMemory.missing.includes(
      "cumulative user and system CPU counters per process"
    )
  );
  const expectedFailedEventCount = 1;
  assert.equal(report.totals.failedEvents, expectedFailedEventCount);
});

test("recognizes supplied experiment and process evidence", () => {
  const event = normalizeEvent({
    ...base,
    id: "measured",
    taskId: "task-1",
    experimentId: "experiment-1",
    variant: "baseline",
    toolExecutionId: "tool-1",
    occurredAt: 1000,
  });
  const report = analyzeScan({
    events: [event],
    processSamples: [
      {
        pid: 42,
        toolExecutionId: "tool-1",
        cumulativeCpuSeconds: 2.5,
        residentMemoryBytes: 1000,
        sampleIntervalSeconds: 1,
      },
    ],
    quotas: [
      {
        label: "Provider snapshot",
        metric: "credits",
        used: 50,
        resetsAt: 2000,
      },
    ],
  });
  assert.ok(
    !report.definitiveResultRequirements.wastedTokens.missing.includes(
      "taskId and taskVersion"
    )
  );
  assert.ok(
    !report.definitiveResultRequirements.cpuAndMemory.missing.includes(
      "cumulative user and system CPU counters per process"
    )
  );
  assert.ok(
    !report.definitiveResultRequirements.subscriptionCapacity.missing.includes(
      "provider-reported reset timestamp"
    )
  );
});
