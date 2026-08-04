import { resolveDaemonOrigin } from "@soleaux/protocol/env";
import type { NextConfig } from "next";

const daemonUrl = resolveDaemonOrigin(process.env.SOLEAUX_DAEMON_URL);
const staticExport = process.env.SOLEAUX_UI_EXPORT === "1";

const nextConfig: NextConfig = {
  transpilePackages: ["@soleaux/ui", "@soleaux/protocol"],
  // Workspace TypeScript is 7.x; Next's build-time type check needs the CLI path.
  experimental: { useTypeScriptCli: true },
  ...(staticExport
    ? { output: "export" as const, trailingSlash: true }
    : {
        output: "standalone" as const,
        rewrites: async () =>
          await Promise.resolve([
            {
              source: "/api/monitor/:path*",
              destination: `${daemonUrl}/api/v1/:path*`,
            },
          ]),
      }),
};

export default nextConfig;
