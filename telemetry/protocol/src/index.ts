export const soleauxProtocolVersion = 2 as const;

export type SessionState =
  "starting" | "active" | "idle" | "ended" | "orphaned";
export type AttributionMethod =
  | "explicit-environment"
  | "registered-root"
  | "ancestor"
  | "process-group"
  | "terminal"
  | "tool-registration"
  | "working-directory"
  | "heuristic"
  | "shared"
  | "unattributed";

export type UsageSource =
  "api-response" | "agent-hook" | "provider-status" | "manual" | "import";
export type QuotaWindowKind =
  "rolling" | "fixed" | "billing-cycle" | "credit-balance";
export type QuotaMetric =
  "tokens" | "credits" | "messages" | "requests" | "cost-usd";
export type ExperimentVariant = "baseline" | "intervention";

export type ExecutionStatus = "running" | "completed" | "failed" | "cancelled";

export type AlertSeverity = "info" | "warning" | "critical";

export type AlertCategory =
  "system" | "quota" | "context" | "performance" | "cost";

export interface ProcessIdentity {
  pid: number;
  startedAtUnixMs: number;
}

export interface EvidenceContext {
  taskId?: string;
  taskVersion?: string;
  completionAssertionId?: string;
  completionAssertionPassed?: boolean;
  experimentId?: string;
  variant?: ExperimentVariant | (string & {});
  configurationHash?: string;
  providerClientVersion?: string;
  repositoryCommit?: string;
}

export interface AgentSession extends EvidenceContext {
  id: string;
  providerId: string;
  displayName: string;
  rootProcess: ProcessIdentity;
  workingDirectory?: string;
  repositoryRoot?: string;
  branch?: string;
  modelId?: string;
  contextWindowTokens?: number;
  startedAt: number;
  endedAt?: number;
  state: SessionState;
}

export interface ProcessSnapshot {
  identity: ProcessIdentity;
  parentPid?: number;
  parentStartedAtUnixMs?: number;
  sessionId?: string;
  toolExecutionId?: string;
  executable: string;
  command: string[];
  cpuPercent: number;
  cumulativeUserCpuSeconds?: number;
  cumulativeSystemCpuSeconds?: number;
  residentMemoryBytes: number;
  sampleIntervalSeconds?: number;
  runtimeSeconds: number;
  attributionMethod: AttributionMethod;
  attributionConfidence?: number;
  sharedProcess?: boolean;
}

export interface ToolExecution extends EvidenceContext {
  id: string;
  sessionId: string;
  category: string;
  toolName: string;
  normalizedArgumentsHash?: string;
  rootProcess?: ProcessIdentity;
  startedAt: number;
  completedAt?: number;
  exitCode?: number;
  status: ExecutionStatus;
  outputBytes?: number;
}

export interface TaskOutcome extends EvidenceContext {
  taskId: string;
  taskVersion: string;
  completionAssertionId: string;
  completionAssertionPassed: boolean;
  experimentId: string;
  variant: ExperimentVariant | (string & {});
  startedAt: number;
  completedAt: number;
  status: "completed" | "failed" | "cancelled";
  metadata?: Record<string, string | number | boolean | null>;
}

export interface AlertSnapshot {
  id: string;
  severity: AlertSeverity;
  category?: AlertCategory;
  title: string;
  description: string;
  sessionId?: string;
  process?: ProcessIdentity;
}

export interface TokenUsage {
  inputTokens: number;
  cachedInputTokens: number;
  cacheWriteTokens: number;
  outputTokens: number;
  reasoningTokens: number;
  totalTokens: number;
}

export interface LlmPerformance {
  requestStartedAt: number;
  firstTokenAt?: number;
  completedAt?: number;
  latencyMs?: number;
  timeToFirstTokenMs?: number;
  tokensPerSecond?: number;
  retryCount: number;
  status: ExecutionStatus;
  errorCode?: string;
}

export interface UsageEvent extends EvidenceContext {
  id: string;
  providerId: string;
  accountId?: string;
  workspaceId?: string;
  sessionId?: string;
  turnId?: string;
  itemId?: string;
  toolExecutionId?: string;
  requestId?: string;
  modelId: string;
  source: UsageSource;
  occurredAt: number;
  usage: TokenUsage;
  performance: LlmPerformance;
  contextWindowTokens?: number;
  contextTokensUsed?: number;
  contextUtilizationPercent?: number;
  estimatedCostUsd?: number;
  creditsConsumed?: number;
  metadata?: Record<string, string | number | boolean | null>;
}

export interface QuotaWindow {
  id: string;
  providerId: string;
  accountId?: string;
  planId?: string;
  label: string;
  kind: QuotaWindowKind;
  metric: QuotaMetric;
  unit?: string;
  limit?: number;
  used: number;
  remaining?: number;
  utilizationPercent?: number;
  windowStartedAt?: number;
  resetsAt?: number;
  durationSeconds?: number;
  source: UsageSource;
  observedAt: number;
  confidence?: number;
  captureMethod?: string;
  providerClientVersion?: string;
}

export interface ProviderUsageSummary {
  providerId: string;
  accountId?: string;
  planId?: string;
  observedAt: number;
  tokens: TokenUsage;
  requestCount: number;
  failedRequestCount: number;
  estimatedCostUsd: number;
  creditsConsumed: number;
  averageLatencyMs?: number;
  averageTimeToFirstTokenMs?: number;
  averageTokensPerSecond?: number;
  quotas: QuotaWindow[];
}

export interface SystemSnapshot {
  cpuPercent: number;
  memoryUsedBytes: number;
  memoryTotalBytes: number;
  processCount: number;
}

export interface SnapshotEvent {
  type: "snapshot";
  protocolVersion: typeof soleauxProtocolVersion;
  sequence: number;
  timestamp: number;
  system: SystemSnapshot;
  sessions: AgentSession[];
  processChanges: ProcessSnapshot[];
  removedProcesses: ProcessIdentity[];
  alerts: AlertSnapshot[];
  providerUsage: ProviderUsageSummary[];
  recentUsage: UsageEvent[];
  activeToolExecutions?: ToolExecution[];
  recentTaskOutcomes?: TaskOutcome[];
}

export type MonitorStreamEvent = SnapshotEvent;

// Wire shape of one MCP tool-call event emitted by the soleaux metrics
// middleware. Field names stay snake_case to match the Python emitter's
// ToolCallEvent.payload() exactly; the daemon ingests it unchanged.
export interface McpToolCallEvent {
  operation: string;
  backend: string;
  tool_name: string;
  duration_ms: number;
  ok: boolean;
  error_type: string | null;
  at: string;
}

export type McpBackendAuthState = "ok" | "auth_error" | "error";

export interface McpBackendSummary {
  backend: string;
  callCount: number;
  errorCount: number;
  p50DurationMs?: number;
  p95DurationMs?: number;
  lastEventAt?: string;
  lastErrorType?: string;
  lastAuthState?: McpBackendAuthState;
}
