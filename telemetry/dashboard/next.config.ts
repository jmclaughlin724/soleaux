import { fileURLToPath } from "node:url";

import {
  daemonApiBase,
  monitorProxyApiBase,
  resolveDaemonOrigin,
} from "@soleaux/protocol/env";
import type { NextConfig } from "next";

const daemonUrl = resolveDaemonOrigin(process.env.SOLEAUX_DAEMON_URL);
const exportBuild = process.env.SOLEAUX_DASHBOARD_EXPORT === "1";

const nextConfig: NextConfig = {
  transpilePackages: ["@soleaux/ui", "@soleaux/protocol"],
  turbopack: { root: fileURLToPath(new URL("../..", import.meta.url)) },
  env: { NEXT_PUBLIC_SOLEAUX_DASHBOARD_EXPORT: exportBuild ? "1" : "" },
  // Workspace TypeScript is 7.x; Next's build-time type check needs the CLI path.
  experimental: { useTypeScriptCli: true },
  ...(exportBuild
    ? { output: "export" as const, trailingSlash: true }
    : {
        output: "standalone" as const,
        rewrites: async () =>
          await Promise.resolve([
            {
              source: `${monitorProxyApiBase}/:path*`,
              destination: `${daemonUrl}${daemonApiBase}/:path*`,
            },
          ]),
      }),
};

export default nextConfig;
