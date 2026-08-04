"use client";

import * as React from "react";
import type {
  McpBackendAuthState,
  McpBackendSummary,
  McpToolCallEvent,
} from "@soleaux/protocol";
import { resolveMonitorApiBase } from "@soleaux/protocol/env";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  KeyRound,
  PlugZap,
  XCircle,
} from "lucide-react";

import { SiteNav } from "../site-nav";

const POLL_INTERVAL_MS = 3_000;
const DRILL_DOWN_EVENT_LIMIT = 15;

const monitorApiBase = resolveMonitorApiBase(
  process.env.NEXT_PUBLIC_SOLEAUX_DASHBOARD_EXPORT
);

const eventTimeFormat = new Intl.DateTimeFormat("en-US", {
  dateStyle: "short",
  timeStyle: "medium",
  timeZone: "UTC",
});

function usePolledJson<T>(url: string) {
  const [data, setData] = React.useState<T | null>(null);
  const [reachable, setReachable] = React.useState(false);

  React.useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const response = await fetch(url, { cache: "no-store" });
        if (!response.ok) {
          throw new Error(`daemon responded ${response.status}`);
        }
        const json = (await response.json()) as T;
        if (!cancelled) {
          setData(json);
          setReachable(true);
        }
      } catch {
        if (!cancelled) {
          setReachable(false);
        }
      }
    };
    void load();
    const timer = setInterval(() => void load(), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [url]);

  return { data, reachable };
}

function formatMs(value?: number) {
  return value == null ? "—" : `${Math.round(value)} ms`;
}

function formatRelativeTime(iso?: string) {
  if (!iso) return "—";
  const elapsedMs = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(elapsedMs)) return "—";
  if (elapsedMs < 60_000) return "just now";
  if (elapsedMs < 3_600_000) return `${Math.floor(elapsedMs / 60_000)}m ago`;
  if (elapsedMs < 86_400_000) return `${Math.floor(elapsedMs / 3_600_000)}h ago`;
  return `${Math.floor(elapsedMs / 86_400_000)}d ago`;
}

type BackendHealth = "healthy" | "auth_required" | "error" | "unknown";

// The daemon only summarizes backends that produced events, so health comes
// from the latest event's auth state; "unknown" covers a missing last event.
function backendHealth(backend: McpBackendSummary): BackendHealth {
  if (!backend.lastEventAt) return "unknown";
  if (backend.lastAuthState === "auth_error") return "auth_required";
  if (backend.lastAuthState === "error") return "error";
  return "healthy";
}

const healthPresentation: Record<
  BackendHealth,
  { label: string; dotClass: string }
> = {
  healthy: { label: "Healthy", dotClass: "bg-emerald-500" },
  auth_required: { label: "Auth required", dotClass: "bg-destructive" },
  error: { label: "Error", dotClass: "bg-amber-500" },
  unknown: { label: "No events", dotClass: "bg-muted-foreground" },
};

function AuthStateBadge({ state }: { readonly state?: McpBackendAuthState }) {
  if (state === "auth_error") {
    return (
      <span className="text-destructive inline-flex items-center gap-1 text-xs font-medium">
        <KeyRound className="size-3.5" /> auth_error
      </span>
    );
  }
  if (state === "error") {
    return <span className="text-amber-600 text-xs font-medium">error</span>;
  }
  if (state === "ok") {
    return <span className="text-muted-foreground text-xs">ok</span>;
  }
  return <span className="text-muted-foreground text-xs">—</span>;
}

function BackendEvents({ backend }: { readonly backend: string }) {
  // The daemon filters, sorts newest-first, and bounds the page server-side
  // so polling never downloads the full retention buffer.
  const { data, reachable } = usePolledJson<McpToolCallEvent[]>(
    `${monitorApiBase}/mcp/events?backend=${encodeURIComponent(backend)}&limit=${DRILL_DOWN_EVENT_LIMIT}`
  );
  const recent = data ?? [];

  return (
    <div className="bg-muted/30 px-4 py-3">
      {!reachable && (
        <p className="text-muted-foreground text-xs">
          Recent events are unavailable; the telemetry daemon is unreachable.
        </p>
      )}
      {reachable && !recent.length && (
        <p className="text-muted-foreground text-xs">
          No recorded calls for this backend.
        </p>
      )}
      {recent.length > 0 && (
        <table className="w-full text-left text-xs">
          <thead className="text-muted-foreground">
            <tr>
              <th className="pb-2">Tool</th>
              <th>Duration</th>
              <th>Result</th>
              <th>Error type</th>
              <th>At (UTC)</th>
            </tr>
          </thead>
          <tbody>
            {recent.map((event, index) => (
              <tr
                key={`${event.at}-${index}`}
                className="border-border border-t"
              >
                <td className="max-w-md truncate py-2 font-mono">
                  {event.tool_name}
                </td>
                <td>{formatMs(event.duration_ms)}</td>
                <td>
                  {event.ok ? (
                    <span className="inline-flex items-center gap-1 text-emerald-600">
                      <CheckCircle2 className="size-3.5" /> ok
                    </span>
                  ) : (
                    <span className="text-destructive inline-flex items-center gap-1">
                      <XCircle className="size-3.5" /> failed
                    </span>
                  )}
                </td>
                <td className="font-mono">{event.error_type ?? "—"}</td>
                <td className="text-muted-foreground">
                  {eventTimeFormat.format(new Date(event.at))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function BackendsPanel({
  backends,
}: {
  readonly backends: McpBackendSummary[];
}) {
  const [expanded, setExpanded] = React.useState<string | null>(null);

  return (
    <article className="bg-card rounded-xl border p-5 shadow-sm">
      <h2 className="font-semibold">MCP gateway backends</h2>
      <p className="text-muted-foreground mt-1 text-sm">
        Aggregated tool calls routed through the soleaux MCP gateway. Select a
        backend to inspect its recent calls.
      </p>
      <div className="mt-5 overflow-x-auto">
        <table className="w-full min-w-[860px] text-left text-sm">
          <thead className="text-muted-foreground border-b">
            <tr>
              <th className="pb-3" />
              <th className="pb-3">Backend</th>
              <th>Health</th>
              <th>Calls</th>
              <th>Errors</th>
              <th>p50</th>
              <th>p95</th>
              <th>Last event</th>
              <th>Auth</th>
            </tr>
          </thead>
          <tbody>
            {backends.map((backend) => {
              const health = backendHealth(backend);
              const presentation = healthPresentation[health];
              const errorRate = backend.callCount
                ? (backend.errorCount / backend.callCount) * 100
                : 0;
              const isExpanded = expanded === backend.backend;
              return (
                <React.Fragment key={backend.backend}>
                  <tr
                    className="hover:bg-muted/40 cursor-pointer border-b last:border-0"
                    onClick={() =>
                      setExpanded(isExpanded ? null : backend.backend)
                    }
                  >
                    <td className="py-3 pr-2">
                      {isExpanded ? (
                        <ChevronDown className="text-muted-foreground size-4" />
                      ) : (
                        <ChevronRight className="text-muted-foreground size-4" />
                      )}
                    </td>
                    <td className="py-3 font-medium">
                      <span className="inline-flex items-center gap-2">
                        <PlugZap className="text-muted-foreground size-4" />
                        {backend.backend}
                      </span>
                    </td>
                    <td>
                      <span className="inline-flex items-center gap-2">
                        <span
                          className={`size-2 rounded-full ${presentation.dotClass}`}
                        />
                        {presentation.label}
                      </span>
                    </td>
                    <td className="font-medium">{backend.callCount}</td>
                    <td>
                      {backend.errorCount}{" "}
                      <span className="text-muted-foreground text-xs">
                        ({errorRate.toFixed(1)}%)
                      </span>
                    </td>
                    <td>{formatMs(backend.p50DurationMs)}</td>
                    <td>{formatMs(backend.p95DurationMs)}</td>
                    <td title={backend.lastEventAt}>
                      {formatRelativeTime(backend.lastEventAt)}
                    </td>
                    <td>
                      <AuthStateBadge state={backend.lastAuthState} />
                    </td>
                  </tr>
                  {isExpanded && (
                    <tr className="border-b last:border-0">
                      <td colSpan={9} className="p-0">
                        {backend.lastAuthState === "auth_error" && (
                          <p className="text-destructive px-4 pt-3 text-xs">
                            Authentication is failing for this backend. Run{" "}
                            <code>
                              soleaux mcp login {backend.backend}
                            </code>{" "}
                            to re-authenticate.
                          </p>
                        )}
                        {backend.lastErrorType && (
                          <p className="text-muted-foreground px-4 pt-3 text-xs">
                            Last error type:{" "}
                            <code>{backend.lastErrorType}</code>
                          </p>
                        )}
                        <BackendEvents backend={backend.backend} />
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
        {!backends.length && (
          <div className="text-muted-foreground grid min-h-40 place-items-center gap-2 py-10 text-center text-sm">
            <p>No MCP gateway traffic recorded yet.</p>
            <p className="max-w-md text-xs">
              This view aggregates tool calls that agents route through the
              soleaux MCP gateway. Backends, call counts, latency percentiles,
              and auth state appear here as agents call backend tools.
            </p>
          </div>
        )}
      </div>
    </article>
  );
}

export default function McpBackendsPage() {
  const { data, reachable } = usePolledJson<McpBackendSummary[]>(
    `${monitorApiBase}/mcp/summary`
  );

  return (
    <main className="bg-muted/20 min-h-svh">
      <header className="border-border bg-background flex min-h-16 items-center justify-between gap-4 border-b px-6 py-3">
        <div>
          <SiteNav active="mcp" />
          <p className="text-muted-foreground mt-1 text-xs">
            MCP gateway backend registry, health, and call telemetry
          </p>
        </div>
        <div className="text-muted-foreground flex items-center gap-2 text-sm">
          <span
            className={`size-2 rounded-full ${reachable ? "bg-emerald-500" : "bg-destructive"}`}
          />
          {reachable ? "Live" : "Disconnected"}
        </div>
      </header>

      <div className="mx-auto grid max-w-[1600px] gap-6 p-6">
        {!reachable && (
          <p className="text-muted-foreground text-sm">
            The telemetry daemon is unreachable; showing the last recorded
            data.
          </p>
        )}
        <BackendsPanel backends={data ?? []} />
      </div>
    </main>
  );
}
