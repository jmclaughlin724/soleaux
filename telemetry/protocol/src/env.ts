/**
 * Environment-owned defaults for the telemetry surface (r16).
 *
 * The daemon base URL is always the bare origin; every consumer appends
 * /api/v1 itself. The default lives here, once.
 */
export const defaultDaemonOrigin = "http://127.0.0.1:43120";

export function resolveDaemonOrigin(configured: string | undefined): string {
  if (configured === undefined || configured === "") {
    return defaultDaemonOrigin;
  }
  return configured.replace(/\/$/u, "");
}
