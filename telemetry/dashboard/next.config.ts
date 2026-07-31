import { resolveDaemonOrigin } from "@soleaux/protocol/env";
import type { NextConfig } from "next";

const daemonUrl = resolveDaemonOrigin(process.env.SOLEAUX_DAEMON_URL);

const nextConfig: NextConfig = {
  transpilePackages: ["@soleaux/ui", "@soleaux/protocol"],
  output: "standalone",
  // Workspace TypeScript is 7.x; Next's build-time type check needs the CLI path.
  experimental: { useTypeScriptCli: true },
  rewrites: async () =>
    await Promise.resolve([
      {
        source: "/api/monitor/:path*",
        destination: `${daemonUrl}/api/v1/:path*`,
      },
    ]),
};

export default nextConfig;
