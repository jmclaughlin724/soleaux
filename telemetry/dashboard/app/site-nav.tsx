import { Radio } from "lucide-react";
import Link from "next/link";

export type DashboardRoute = "overview" | "mcp";

function linkClass(active: boolean) {
  return `rounded-md px-2 py-1 ${
    active
      ? "bg-muted text-foreground font-medium"
      : "text-muted-foreground hover:text-foreground"
  }`;
}

export function SiteNav({ active }: { readonly active: DashboardRoute }) {
  return (
    <div className="flex items-center gap-4">
      <div className="flex items-center gap-2 font-semibold">
        <Radio className="size-4" /> Soleaux
      </div>
      <nav className="flex items-center gap-1 text-sm">
        <Link className={linkClass(active === "overview")} href="/">
          Overview
        </Link>
        <Link className={linkClass(active === "mcp")} href="/mcp">
          MCP backends
        </Link>
      </nav>
    </div>
  );
}
