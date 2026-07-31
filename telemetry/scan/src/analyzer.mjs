import { createHash } from "node:crypto";

const TOOL_PATTERNS = [
  ["compaction", /compact|compaction/iu],
  ["web-search", /web.?search|search_query|image_query/iu],
  ["mcp", /(^|[\s._-])mcp([\s._-]|$)|model.?context.?protocol/iu],
  ["rg", /(^|\s|\/)(rg|ripgrep)(\s|$)/iu],
  [
    "tests",
    /pytest|vitest|jest|playwright|cargo test|pnpm test|npm test|yarn test/iu,
  ],
  ["git", /(^|\s)git(\s|$)/iu],
  ["file-read", /read_file|fetch_file|cat\s|sed\s+-n|head\s|tail\s/iu],
  ["file-write", /write_file|apply_patch|update_file|create_file/iu],
  ["bash", /bash|shell|terminal|exec|command_execution/iu],
];

const ZERO = 0;
const ID_HASH_HEX_LENGTH = 24;
const SIGNATURE_HASH_HEX_LENGTH = 20;

const number = (value) =>
  Number.isFinite(Number(value)) ? Number(value) : ZERO;
const first = (...values) =>
  values.find((value) => value !== undefined && value !== null);

export function classifyTool(event) {
  const explicit = first(event.toolCategory, event.category);
  if (explicit) {
    return String(explicit);
  }
  const tool = String(
    first(
      event.toolName,
      event.tool_name,
      event.name,
      event.type,
      event.kind,
      ""
    )
  );
  const command = String(
    first(
      event.command,
      event.input?.command,
      event.arguments?.command,
      event.metadata?.command,
      ""
    )
  );
  const haystack = `${tool} ${command}`;
  for (const [category, pattern] of TOOL_PATTERNS) {
    if (pattern.test(haystack)) {
      return category;
    }
  }
  return tool ? "other-tool" : "model";
}

function resolveUsage(raw) {
  return raw.usage ?? raw.response?.usage ?? raw.message?.usage ?? {};
}

function extractUsageTokens(raw, usage) {
  const inputTokens = number(
    first(
      usage.inputTokens,
      usage.input_tokens,
      usage.prompt_tokens,
      raw.inputTokens
    )
  );
  const outputTokens = number(
    first(
      usage.outputTokens,
      usage.output_tokens,
      usage.completion_tokens,
      raw.outputTokens
    )
  );
  const cachedInputTokens = number(
    first(
      usage.cachedInputTokens,
      usage.cached_input_tokens,
      usage.input_cached_tokens,
      usage.input_tokens_details?.cached_tokens,
      usage.prompt_tokens_details?.cached_tokens,
      raw.cachedInputTokens
    )
  );
  const cacheWriteTokens = number(
    first(
      usage.cacheWriteTokens,
      usage.cache_creation_input_tokens,
      raw.cacheWriteTokens
    )
  );
  const reasoningTokens = number(
    first(
      usage.reasoningTokens,
      usage.reasoning_output_tokens,
      usage.output_tokens_details?.reasoning_tokens,
      usage.completion_tokens_details?.reasoning_tokens,
      raw.reasoningTokens
    )
  );
  const totalTokens = number(
    first(
      usage.totalTokens,
      usage.total_tokens,
      raw.totalTokens,
      inputTokens + outputTokens
    )
  );
  return {
    inputTokens,
    outputTokens,
    cachedInputTokens,
    cacheWriteTokens,
    reasoningTokens,
    totalTokens,
  };
}

function resolveTimestamps(raw) {
  const startedAt = number(
    first(
      raw.requestStartedAt,
      raw.performance?.requestStartedAt,
      raw.startedAt,
      raw.timestamp,
      raw.occurredAt,
      Date.now()
    )
  );
  const completedAt = number(
    first(
      raw.completedAt,
      raw.performance?.completedAt,
      raw.endedAt,
      startedAt + number(first(raw.latencyMs, raw.performance?.latencyMs))
    )
  );
  return { startedAt, completedAt };
}

function resolveStatus(raw) {
  return String(
    first(
      raw.status,
      raw.performance?.status,
      raw.error ? "failed" : "completed"
    )
  );
}

export function normalizeEvent(raw, source = "import") {
  const usage = resolveUsage(raw);
  const {
    inputTokens,
    outputTokens,
    cachedInputTokens,
    cacheWriteTokens,
    reasoningTokens,
    totalTokens,
  } = extractUsageTokens(raw, usage);
  const { startedAt, completedAt } = resolveTimestamps(raw);
  const outputText = String(
    first(raw.output, raw.result, raw.content, raw.toolOutput, "")
  );
  const status = resolveStatus(raw);
  const category = classifyTool(raw);
  const signatureSource = JSON.stringify({
    category,
    tool: first(raw.toolName, raw.tool_name, raw.name),
    command: first(raw.command, raw.input?.command, raw.arguments?.command),
    input: first(raw.input, raw.arguments),
  });
  return {
    id: String(
      first(
        raw.id,
        raw.requestId,
        raw.request_id,
        createHash("sha256")
          .update(`${source}:${startedAt}:${signatureSource}`)
          .digest("hex")
          .slice(ZERO, ID_HASH_HEX_LENGTH)
      )
    ),
    source,
    providerId: String(
      first(raw.providerId, raw.provider, raw.vendor, "unknown")
    ),
    sessionId: first(
      raw.sessionId,
      raw.session_id,
      raw.conversationId,
      raw.conversation_id
    ),
    taskId: first(raw.taskId, raw.task_id),
    experimentId: first(raw.experimentId, raw.experiment_id),
    variant: first(raw.variant, raw.metadata?.variant),
    toolExecutionId: first(raw.toolExecutionId, raw.tool_execution_id),
    modelId: String(first(raw.modelId, raw.model, "unknown")),
    category,
    toolName: String(first(raw.toolName, raw.tool_name, raw.name, category)),
    signature: createHash("sha256")
      .update(signatureSource)
      .digest("hex")
      .slice(ZERO, SIGNATURE_HASH_HEX_LENGTH),
    startedAt,
    completedAt,
    latencyMs: number(
      first(raw.latencyMs, raw.performance?.latencyMs, completedAt - startedAt)
    ),
    status,
    retryCount: number(first(raw.retryCount, raw.performance?.retryCount)),
    inputTokens,
    outputTokens,
    cachedInputTokens,
    cacheWriteTokens,
    reasoningTokens,
    totalTokens,
    contextWindowTokens: number(
      first(raw.contextWindowTokens, raw.context_window_tokens)
    ),
    contextTokensUsed: number(
      first(raw.contextTokensUsed, raw.context_tokens_used)
    ),
    cpuPercent: number(first(raw.cpuPercent, raw.process?.cpuPercent)),
    cumulativeCpuSeconds: number(
      first(raw.cumulativeCpuSeconds, raw.process?.cumulativeCpuSeconds)
    ),
    memoryBytes: number(
      first(
        raw.residentMemoryBytes,
        raw.memoryBytes,
        raw.process?.residentMemoryBytes
      )
    ),
    sampleIntervalSeconds: number(
      first(raw.sampleIntervalSeconds, raw.durationSeconds)
    ),
    outputBytes: Buffer.byteLength(outputText),
    command: String(
      first(raw.command, raw.input?.command, raw.arguments?.command, "")
    ),
    estimatedCostUsd: number(
      first(raw.estimatedCostUsd, raw.costUsd, raw.cost_usd)
    ),
    metadata: raw.metadata ?? {},
  };
}

function definitiveRequirements(normalized, processRows, quotas) {
  const hasTaskIds = normalized.some((event) => event.taskId);
  const hasExperimentIds = normalized.some((event) => event.experimentId);
  const hasVariants = normalized.some((event) => event.variant);
  const hasToolExecutionIds = normalized.some((event) => event.toolExecutionId);
  const hasCumulativeCpu = processRows.some(
    (sample) => sample.cumulativeCpuSeconds > ZERO
  );
  const hasIntervals = processRows.some(
    (sample) => sample.sampleIntervalSeconds > ZERO
  );
  const hasQuotaReset = quotas.some(
    (quota) => quota.resetsAt || quota.reset_at
  );
  const hasQuotaUnit = quotas.some((quota) => quota.metric || quota.unit);

  return {
    toolLevelTokens: {
      status: "requires-evidence",
      missing: [
        "provider-native item-level token fields, or matched counterfactual runs",
        ...(hasTaskIds ? [] : ["taskId on every compared run"]),
        ...(hasExperimentIds
          ? []
          : ["experimentId linking baseline and intervention runs"]),
        ...(hasVariants
          ? []
          : ["variant identifying baseline and intervention"]),
        ...(hasToolExecutionIds
          ? []
          : ["toolExecutionId linking tool lifecycle to requests"]),
        "machine-verifiable completion assertion for both runs",
      ],
      acceptance:
        "Both matched runs complete the same task; incremental tokens equal original-result run minus validated-alternative run.",
    },
    wastedTokens: {
      status: "requires-evidence",
      missing: [
        ...(hasTaskIds ? [] : ["taskId and taskVersion"]),
        ...(hasExperimentIds ? [] : ["experimentId"]),
        ...(hasVariants ? [] : ["baseline/intervention variant"]),
        "completionAssertionId and pass/fail outcome",
        "matched provider, model, client version, repository state, and configuration",
      ],
      acceptance:
        "The intervention removes the candidate work while preserving the same validated task outcome; waste equals baseline tokens minus intervention tokens.",
    },
    cpuAndMemory: {
      status: "requires-evidence",
      missing: [
        ...(hasToolExecutionIds
          ? []
          : ["toolExecutionId propagated into child processes"]),
        ...(hasCumulativeCpu
          ? []
          : ["cumulative user and system CPU counters per process"]),
        ...(hasIntervals ? [] : ["known process sample interval"]),
        "PID plus process start time",
        "observed parent identity at each sample",
        "process exit record",
        "shared-process handling",
      ],
      acceptance:
        "CPU is computed from cumulative counter deltas; memory byte-seconds are integrated over known intervals; PID reuse and shared processes are handled explicitly.",
    },
    subscriptionCapacity: {
      status: "requires-evidence",
      missing: [
        ...(hasQuotaReset ? [] : ["provider-reported reset timestamp"]),
        ...(hasQuotaUnit ? [] : ["provider-reported metric or unit"]),
        "official provider window identifier or authenticated usage-surface observation",
        "paired observations immediately before and after matched workloads",
      ],
      acceptance:
        "Capacity change is the provider-reported before/after delta in the same identified window; no token-to-days conversion is used.",
    },
    soleauxSavings: {
      status: "requires-evidence",
      missing: [
        ...(hasTaskIds ? [] : ["taskId and taskVersion"]),
        ...(hasExperimentIds ? [] : ["experimentId"]),
        ...(hasVariants ? [] : ["baseline and named Soleaux-control variants"]),
        "active-control configuration version",
        "completion assertion",
        "repeated matched runs and uncertainty calculation",
      ],
      acceptance:
        "Report measured baseline minus intervention deltas only for successful comparable tasks, scoped to the tested provider, model, client version, task class, and control version.",
    },
  };
}

export function analyzeScan({
  events = [],
  quotas = [],
  processSamples = [],
} = {}) {
  const normalized = events.map((event) =>
    event.category ? event : normalizeEvent(event)
  );
  const categories = new Map();
  const exactRepeats = new Map();
  const observations = [];

  for (const event of normalized) {
    const row = categories.get(event.category) ?? {
      category: event.category,
      calls: 0,
      tokens: 0,
      inputTokens: 0,
      cachedInputTokens: 0,
      outputTokens: 0,
      reasoningTokens: 0,
      latencyMs: 0,
      outputBytes: 0,
      failedCalls: 0,
      retriedCalls: 0,
      peakMemoryBytes: 0,
    };
    row.calls += 1;
    row.tokens += event.totalTokens;
    row.inputTokens += event.inputTokens;
    row.cachedInputTokens += event.cachedInputTokens;
    row.outputTokens += event.outputTokens;
    row.reasoningTokens += event.reasoningTokens;
    row.latencyMs += event.latencyMs;
    row.outputBytes += event.outputBytes;
    row.failedCalls += Number(event.status === "failed");
    row.retriedCalls += Number(event.retryCount > ZERO);
    row.peakMemoryBytes = Math.max(row.peakMemoryBytes, event.memoryBytes);
    categories.set(event.category, row);

    const prior = exactRepeats.get(event.signature);
    if (prior) {
      observations.push({
        type: "exact-repeat",
        category: event.category,
        eventIds: [prior.id, event.id],
        tokenUsageOnLaterEvent: event.totalTokens,
        statement:
          "The normalized tool signature occurred more than once. A matched task experiment is required to determine whether the later call was avoidable.",
        evidenceClass: "measured",
      });
    } else {
      exactRepeats.set(event.signature, event);
    }

    if (event.status === "failed" || event.retryCount > ZERO) {
      observations.push({
        type: "failed-or-retried",
        category: event.category,
        eventIds: [event.id],
        tokenUsage: event.totalTokens,
        retryCount: event.retryCount,
        status: event.status,
        statement:
          "The source record reports a failure or retry. Task-outcome lineage and a matched intervention are required to determine avoidability.",
        evidenceClass: "measured",
      });
    }
  }

  const processRows = processSamples.map((sample) => ({
    pid: sample.pid,
    startedAtUnixMs: first(sample.startedAtUnixMs, sample.startTimeUnixMs),
    parentPid: sample.parentPid,
    timestamp: sample.timestamp,
    category: classifyTool(sample),
    toolExecutionId: first(sample.toolExecutionId, sample.tool_execution_id),
    cpuPercent: number(sample.cpuPercent),
    cumulativeCpuSeconds:
      number(first(sample.cumulativeCpuSeconds, sample.userCpuSeconds)) +
      number(sample.systemCpuSeconds),
    residentMemoryBytes: number(
      first(sample.residentMemoryBytes, sample.memoryBytes)
    ),
    sampleIntervalSeconds: number(
      first(sample.sampleIntervalSeconds, sample.durationSeconds)
    ),
    executable: sample.executable,
    command: sample.command,
    evidenceClass: "measured",
  }));

  const requirements = definitiveRequirements(normalized, processRows, quotas);

  return {
    schemaVersion: 3,
    generatedAt: Date.now(),
    scan: {
      eventCount: normalized.length,
      processSampleCount: processRows.length,
      quotaCount: quotas.length,
    },
    totals: {
      totalTokens: normalized.reduce(
        (sum, event) => sum + event.totalTokens,
        ZERO
      ),
      inputTokens: normalized.reduce(
        (sum, event) => sum + event.inputTokens,
        ZERO
      ),
      cachedInputTokens: normalized.reduce(
        (sum, event) => sum + event.cachedInputTokens,
        ZERO
      ),
      outputTokens: normalized.reduce(
        (sum, event) => sum + event.outputTokens,
        ZERO
      ),
      reasoningTokens: normalized.reduce(
        (sum, event) => sum + event.reasoningTokens,
        ZERO
      ),
      failedEvents: normalized.filter((event) => event.status === "failed")
        .length,
      retriedEvents: normalized.filter((event) => event.retryCount > ZERO)
        .length,
    },
    categories: categories
      .values()
      .toArray()
      .toSorted((a, b) => b.tokens - a.tokens || b.calls - a.calls),
    observations,
    processSamples: processRows,
    quotas,
    definitiveResultRequirements: requirements,
    provenance: {
      measured: [
        "provider-reported usage fields",
        "explicit tool events",
        "process samples",
        "provider-reported quota snapshots",
      ],
      derived: [
        "category sums",
        "failure and retry counts",
        "exact normalized-signature repeats",
        "evidence-gap inventory",
      ],
      estimates: [],
      methodology: "docs/soleaux/DEFINITIVE_MEASUREMENT_PLAN.md",
    },
  };
}

const reportNumberFormat = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 1,
});
const formatNumber = (value) =>
  reportNumberFormat.format(Number(value ?? ZERO));

export function renderMarkdown(report) {
  const generatedDate = new Date(report.generatedAt);
  const requirementLines = Object.entries(
    report.definitiveResultRequirements
  ).flatMap(([name, value]) => [
    `### ${name}`,
    "",
    `Status: **${value.status}**`,
    "",
    ...value.missing.map((item) => `- ${item}`),
    "",
    `Acceptance criterion: ${value.acceptance}`,
    "",
  ]);
  const lines = [
    "# Soleaux Evidence Report",
    "",
    `Generated: ${generatedDate.toISOString()}`,
    "",
    "## Measured totals",
    "",
    `- Total provider-reported tokens: **${formatNumber(report.totals.totalTokens)}**`,
    `- Input tokens: **${formatNumber(report.totals.inputTokens)}**`,
    `- Cached-input subcategory: **${formatNumber(report.totals.cachedInputTokens)}**`,
    `- Output tokens: **${formatNumber(report.totals.outputTokens)}**`,
    `- Reasoning-token subcategory when reported: **${formatNumber(report.totals.reasoningTokens)}**`,
    `- Failed events: **${formatNumber(report.totals.failedEvents)}**`,
    `- Retried events: **${formatNumber(report.totals.retriedEvents)}**`,
    "",
    "## Usage by observed category",
    "",
    "| Category | Calls | Total tokens | Input | Cached input | Output | Failures | Retries | Output bytes |",
    "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ...report.categories.map(
      (row) =>
        `| ${row.category} | ${formatNumber(row.calls)} | ${formatNumber(row.tokens)} | ${formatNumber(row.inputTokens)} | ${formatNumber(row.cachedInputTokens)} | ${formatNumber(row.outputTokens)} | ${formatNumber(row.failedCalls)} | ${formatNumber(row.retriedCalls)} | ${formatNumber(row.outputBytes)} |`
    ),
    "",
    "## Observations",
    "",
    ...(report.observations.length
      ? report.observations.map(
          (item) => `- **${item.type}** (${item.category}): ${item.statement}`
        )
      : ["- No repeat, failure, or retry observations were recorded."]),
    "",
    "## Evidence required for definitive results",
    "",
    ...requirementLines,
  ];
  return lines.join("\n");
}
