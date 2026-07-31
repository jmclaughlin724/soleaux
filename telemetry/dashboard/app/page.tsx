"use client";

import * as React from "react";
import type {
  AgentSession,
  AlertSnapshot,
  ProcessSnapshot,
  ProviderUsageSummary,
  SnapshotEvent,
  UsageEvent,
} from "@soleaux/protocol";
import {
  Activity,
  Cpu,
  Gauge,
  type LucideIcon,
  MemoryStick,
  Timer,
  Workflow,
} from "lucide-react";

import { SiteNav } from "./site-nav";

const initialSnapshot: SnapshotEvent = {
  type: "snapshot",
  protocolVersion: 2,
  sequence: 0,
  timestamp: 0,
  system: {
    cpuPercent: 0,
    memoryUsedBytes: 0,
    memoryTotalBytes: 0,
    processCount: 0,
  },
  sessions: [],
  processChanges: [],
  removedProcesses: [],
  alerts: [],
  providerUsage: [],
  recentUsage: [],
};

const compactTokenFormat = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 1,
});
const standardTokenFormat = new Intl.NumberFormat("en-US", {
  notation: "standard",
  maximumFractionDigits: 1,
});
const resetDateFormat = new Intl.DateTimeFormat("en-US", {
  dateStyle: "short",
  timeStyle: "short",
  timeZone: "UTC",
});

function formatBytes(value: number) {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(
    Math.floor(Math.log(value) / Math.log(1024)),
    units.length - 1
  );
  return `${(value / 1024 ** index).toFixed(index > 2 ? 1 : 0)} ${units[index]}`;
}

function formatTokens(value: number) {
  const format = value >= 10_000 ? compactTokenFormat : standardTokenFormat;
  return format.format(value);
}

function formatReset(value?: number) {
  if (!value) return "Reset unknown";
  return `Resets ${resetDateFormat.format(new Date(Number(value)))}`;
}

function commandLabel(process: ProcessSnapshot) {
  return process.command.length
    ? process.command.join(" ")
    : process.executable;
}

interface Metric {
  label: string;
  value: string;
  icon: LucideIcon;
}

function DashboardHeader({
  sessions,
  selectedSession,
  onSelectedSessionChange,
  connected,
}: {
  readonly sessions: AgentSession[];
  readonly selectedSession: string;
  readonly onSelectedSessionChange: (value: string) => void;
  readonly connected: boolean;
}) {
  return (
    <header className="border-border bg-background flex min-h-16 items-center justify-between gap-4 border-b px-6 py-3">
      <div>
        <SiteNav active="overview" />
        <p className="text-muted-foreground mt-1 text-xs">
          Agent, model, quota, context, and system observability
        </p>
      </div>
      <div className="flex items-center gap-3">
        <select
          className="bg-background rounded-md border px-3 py-2 text-sm"
          value={selectedSession}
          onChange={(event) => onSelectedSessionChange(event.target.value)}
        >
          <option value="all">All sessions</option>
          {sessions.map((session) => (
            <option key={session.id} value={session.id}>
              {session.displayName}
            </option>
          ))}
        </select>
        <div className="text-muted-foreground flex items-center gap-2 text-sm">
          <span
            className={`size-2 rounded-full ${connected ? "bg-emerald-500" : "bg-destructive"}`}
          />
          {connected ? "Live" : "Disconnected"}
        </div>
      </div>
    </header>
  );
}

function MetricsSection({ metrics }: { readonly metrics: Metric[] }) {
  return (
    <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-6">
      {metrics.map(({ label, value, icon: Icon }) => (
        <article
          key={label}
          className="bg-card rounded-xl border p-5 shadow-sm"
        >
          <div className="text-muted-foreground flex items-center justify-between text-sm">
            {label}
            <Icon className="size-4" />
          </div>
          <p className="mt-3 text-2xl font-semibold tracking-tight">{value}</p>
        </article>
      ))}
    </section>
  );
}

function ProviderUsagePanel({
  providerUsage,
}: {
  readonly providerUsage: ProviderUsageSummary[];
}) {
  return (
    <article className="bg-card rounded-xl border p-5 shadow-sm">
      <h2 className="font-semibold">Provider usage and performance</h2>
      <p className="text-muted-foreground mt-1 text-sm">
        Exact API response metering plus provider-reported subscription
        snapshots.
      </p>
      <div className="mt-4 space-y-4">
        {providerUsage.map((provider) => (
          <div key={provider.providerId} className="rounded-lg border p-4">
            <div className="flex items-center justify-between">
              <strong className="capitalize">{provider.providerId}</strong>
              <span className="text-muted-foreground text-xs">
                {provider.requestCount} requests
              </span>
            </div>
            <div className="mt-3 grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
              <span>{formatTokens(provider.tokens.inputTokens)} input</span>
              <span>{formatTokens(provider.tokens.outputTokens)} output</span>
              <span>
                {formatTokens(provider.tokens.cachedInputTokens)} cached
              </span>
              <span>
                {provider.averageTokensPerSecond
                  ? `${provider.averageTokensPerSecond.toFixed(1)} tok/s`
                  : "— throughput"}
              </span>
            </div>
            <div className="mt-4 space-y-2">
              {provider.quotas.map((quota) => (
                <div key={quota.id}>
                  <div className="flex justify-between text-xs">
                    <span>{quota.label}</span>
                    <span>
                      {quota.utilizationPercent?.toFixed(1) ?? "—"}% ·{" "}
                      {formatReset(quota.resetsAt)}
                    </span>
                  </div>
                  <div className="bg-muted mt-1 h-2 overflow-hidden rounded-full">
                    <div
                      className="bg-foreground h-full"
                      style={{
                        width: `${Math.min(100, quota.utilizationPercent ?? 0)}%`,
                      }}
                    />
                  </div>
                </div>
              ))}
              {!provider.quotas.length && (
                <p className="text-muted-foreground text-xs">
                  No provider quota snapshot has been recorded.
                </p>
              )}
            </div>
          </div>
        ))}
        {!providerUsage.length && (
          <div className="text-muted-foreground grid min-h-32 place-items-center text-sm">
            Record usage with the SDK, agent adapter, or{" "}
            <code>soleaux usage</code>.
          </div>
        )}
      </div>
    </article>
  );
}

function RecentUsagePanel({ events }: { readonly events: UsageEvent[] }) {
  return (
    <article className="bg-card rounded-xl border p-5 shadow-sm">
      <h2 className="font-semibold">Context windows and recent requests</h2>
      <p className="text-muted-foreground mt-1 text-sm">
        Context pressure, latency, errors, cached tokens, and model throughput.
      </p>
      <div className="mt-4 max-h-96 space-y-3 overflow-auto">
        {events.map((event) => (
          <div key={event.id} className="rounded-lg border p-3 text-sm">
            <div className="flex items-center justify-between gap-3">
              <strong>
                {event.providerId} · {event.modelId}
              </strong>
              <span className="text-muted-foreground text-xs">
                {event.performance.status}
              </span>
            </div>
            <div className="text-muted-foreground mt-2 grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
              <span>{formatTokens(event.usage.totalTokens)} tokens</span>
              <span>
                {event.performance.latencyMs
                  ? `${Math.round(event.performance.latencyMs)} ms`
                  : "— latency"}
              </span>
              <span>
                {event.performance.timeToFirstTokenMs
                  ? `${Math.round(event.performance.timeToFirstTokenMs)} ms TTFT`
                  : "— TTFT"}
              </span>
              <span>
                {event.contextUtilizationPercent != null
                  ? `${event.contextUtilizationPercent.toFixed(1)}% context`
                  : "— context"}
              </span>
            </div>
          </div>
        ))}
        {!events.length && (
          <div className="text-muted-foreground grid min-h-32 place-items-center text-sm">
            No LLM requests recorded.
          </div>
        )}
      </div>
    </article>
  );
}

function ProcessesPanel({
  processes,
  sessions,
}: {
  readonly processes: ProcessSnapshot[];
  readonly sessions: AgentSession[];
}) {
  return (
    <article className="bg-card rounded-xl border p-5 shadow-sm">
      <h2 className="font-semibold">Top resource consumers</h2>
      <p className="text-muted-foreground mt-1 text-sm">
        Live processes attributed to registered agent sessions and descendants.
      </p>
      <div className="mt-5 overflow-x-auto">
        <table className="w-full min-w-[760px] text-left text-sm">
          <thead className="text-muted-foreground border-b">
            <tr>
              <th className="pb-3">Process</th>
              <th>Session</th>
              <th>PID</th>
              <th>CPU</th>
              <th>Memory</th>
              <th>Attribution</th>
            </tr>
          </thead>
          <tbody>
            {processes.slice(0, 25).map((process) => (
              <tr
                key={`${process.identity.pid}-${process.identity.startedAtUnixMs}`}
                className="border-b last:border-0"
              >
                <td
                  className="max-w-md truncate py-3 font-mono text-xs"
                  title={commandLabel(process)}
                >
                  {commandLabel(process)}
                </td>
                <td>
                  {sessions.find((session) => session.id === process.sessionId)
                    ?.displayName ?? "Unattributed"}
                </td>
                <td className="font-mono">{process.identity.pid}</td>
                <td className="font-medium">
                  {process.cpuPercent.toFixed(1)}%
                </td>
                <td>{formatBytes(process.residentMemoryBytes)}</td>
                <td>
                  {process.attributionMethod} ·{" "}
                  {Math.round((process.attributionConfidence ?? 0) * 100)}%
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!processes.length && (
          <div className="text-muted-foreground grid min-h-40 place-items-center text-sm">
            No matching processes
          </div>
        )}
      </div>
    </article>
  );
}

function AlertsPanel({ alerts }: { readonly alerts: AlertSnapshot[] }) {
  return (
    <article className="bg-card rounded-xl border p-5 shadow-sm">
      <h2 className="font-semibold">Active findings</h2>
      <p className="text-muted-foreground mt-1 text-sm">
        System, quota, context, performance, and cost alerts.
      </p>
      <div className="mt-4 space-y-3">
        {alerts.map((alert) => (
          <div key={alert.id} className="rounded-lg border p-3">
            <div className="flex items-center justify-between gap-3">
              <strong className="text-sm">{alert.title}</strong>
              <span className="text-muted-foreground text-xs uppercase">
                {alert.severity}
              </span>
            </div>
            <p className="text-muted-foreground mt-1 text-sm">
              {alert.description}
            </p>
          </div>
        ))}
        {!alerts.length && (
          <div className="text-muted-foreground grid min-h-40 place-items-center text-sm">
            No active findings
          </div>
        )}
      </div>
    </article>
  );
}

export default function SoleauxDashboard() {
  const [snapshot, setSnapshot] = React.useState(initialSnapshot);
  const [connected, setConnected] = React.useState(false);
  const [selectedSession, setSelectedSession] = React.useState("all");

  React.useEffect(() => {
    const stream = new EventSource("/api/monitor/stream");
    const handleOpen = () => setConnected(true);
    const handleError = () => setConnected(false);
    const handleSnapshot = (event: MessageEvent<string>) => {
      const snapshot: SnapshotEvent = JSON.parse(event.data);
      setSnapshot(snapshot);
    };
    stream.addEventListener("open", handleOpen);
    stream.addEventListener("error", handleError);
    stream.addEventListener("snapshot", handleSnapshot);
    return () => {
      stream.removeEventListener("open", handleOpen);
      stream.removeEventListener("error", handleError);
      stream.removeEventListener("snapshot", handleSnapshot);
      stream.close();
    };
  }, []);

  const processes = snapshot.processChanges
    .filter(
      (process) =>
        selectedSession === "all" || process.sessionId === selectedSession
    )
    .sort((a, b) => b.cpuPercent - a.cpuPercent);
  const visibleAlerts = snapshot.alerts.filter(
    (alert) => selectedSession === "all" || alert.sessionId === selectedSession
  );
  const providerUsage = snapshot.providerUsage;
  const totalTokens = providerUsage.reduce(
    (sum, provider) => sum + provider.tokens.totalTokens,
    0
  );
  const totalRequests = providerUsage.reduce(
    (sum, provider) => sum + provider.requestCount,
    0
  );
  const averageLatency = providerUsage
    .map((provider) => provider.averageLatencyMs)
    .filter((value): value is number => value != null);
  const latency = averageLatency.length
    ? averageLatency.reduce((a, b) => a + b, 0) / averageLatency.length
    : 0;

  const metrics: Metric[] = [
    {
      label: "CPU",
      value: `${snapshot.system.cpuPercent.toFixed(1)}%`,
      icon: Cpu,
    },
    {
      label: "Memory",
      value: formatBytes(snapshot.system.memoryUsedBytes),
      icon: MemoryStick,
    },
    { label: "LLM tokens", value: formatTokens(totalTokens), icon: Gauge },
    {
      label: "LLM requests",
      value: standardTokenFormat.format(totalRequests),
      icon: Activity,
    },
    {
      label: "Avg latency",
      value: latency ? `${Math.round(latency)} ms` : "—",
      icon: Timer,
    },
    {
      label: "Processes",
      value: standardTokenFormat.format(snapshot.system.processCount),
      icon: Workflow,
    },
  ];

  return (
    <main className="bg-muted/20 min-h-svh">
      <DashboardHeader
        sessions={snapshot.sessions}
        selectedSession={selectedSession}
        onSelectedSessionChange={setSelectedSession}
        connected={connected}
      />

      <div className="mx-auto grid max-w-[1600px] gap-6 p-6">
        <MetricsSection metrics={metrics} />

        <section className="grid gap-6 xl:grid-cols-2">
          <ProviderUsagePanel providerUsage={providerUsage} />
          <RecentUsagePanel events={snapshot.recentUsage} />
        </section>

        <section className="grid gap-6 lg:grid-cols-[1.65fr_1fr]">
          <ProcessesPanel processes={processes} sessions={snapshot.sessions} />
          <AlertsPanel alerts={visibleAlerts} />
        </section>
      </div>
    </main>
  );
}
