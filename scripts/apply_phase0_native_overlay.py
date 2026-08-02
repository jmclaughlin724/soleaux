#!/usr/bin/env python3
"""Apply the binding Soleaux Phase 0 changes to the last green native source."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def replace_exact(path: Path, old: str, new: str, *, label: str) -> None:
    source = path.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one preimage, found {count}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: apply_phase0_native_overlay.py <native-workspace>")
    root = Path(sys.argv[1]).resolve()
    cargo = root / "Cargo.toml"
    mcp = root / "daemon/mcp/src/lib.rs"
    engine = root / "daemon/engine/src/lib.rs"
    profile = root / "daemon/mcp/src/profile.rs"
    for path in (cargo, mcp, engine, profile):
        if not path.is_file():
            raise SystemExit(f"missing Phase 0 source precondition: {path}")

    replace_exact(
        cargo,
        'version = "0.4.0-dev.4"',
        'version = "0.4.0-dev.5"',
        label="workspace version",
    )
    replace_exact(
        mcp,
        "//! Desktop/mobile remote operations are deliberately not exposed here. The\n"
        "//! public profile contains eleven tools by default and fourteen at most when\n"
        "//! PostgreSQL, Turborepo, and Next.js intelligence are enabled.",
        "//! Desktop/mobile remote operations are deliberately not exposed here. The\n"
        "//! Phase 0 runtime keeps eleven transitional tools by default and permits one\n"
        "//! optional substitution while the canonical profile remains capped at twelve.",
        label="MCP module contract",
    )
    replace_exact(
        mcp,
        "pub mod http;\n",
        "pub mod http;\npub mod profile;\n",
        label="profile module registration",
    )
    replace_exact(
        mcp,
        "pub const PUBLIC_ROOT_TOOL_MAX: usize = 14;",
        "pub const PUBLIC_ROOT_TOOL_MAX: usize = profile::HARD_CEILING;",
        label="runtime tool ceiling",
    )
    replace_exact(
        mcp,
        "async fn public_profile_is_eleven_by_default_and_fourteen_at_most()",
        "async fn transitional_profile_is_eleven_by_default_and_twelve_at_most()",
        label="profile test name",
    )
    replace_exact(
        mcp,
        '''        let expanded = server
            .clone()
            .enable_optional_tool(OPTIONAL_POSTGRES)
            .expect("postgres")
            .enable_optional_tool(OPTIONAL_TURBOREPO)
            .expect("turbo")
            .enable_optional_tool(OPTIONAL_NEXTJS)
            .expect("next");
        assert_eq!(expanded.tools().len(), PUBLIC_ROOT_TOOL_MAX);''',
        '''        let expanded = server
            .clone()
            .enable_optional_tool(OPTIONAL_POSTGRES)
            .expect("postgres");
        assert_eq!(expanded.tools().len(), PUBLIC_ROOT_TOOL_MAX);
        let overflow = match expanded.enable_optional_tool(OPTIONAL_TURBOREPO) {
            Ok(_) => panic!("a second optional tool must exceed the transitional ceiling"),
            Err(error) => error,
        };
        assert!(overflow.to_string().contains("ceiling exceeded"));''',
        label="transitional optional ceiling test",
    )
    replace_exact(
        mcp,
        '''        let server = PublicMcpServer::with_store(temp.path(), temp.path().join("index.sqlite3"))
            .expect("server")
            .enable_optional_tool(OPTIONAL_TURBOREPO)
            .expect("turbo")
            .enable_optional_tool(OPTIONAL_NEXTJS)
            .expect("next");
        server.prepare().await.expect("prepare");
        let packages = server
            .call(OPTIONAL_TURBOREPO, &json!({}))
            .expect("packages");''',
        '''        let turbo_server =
            PublicMcpServer::with_store(temp.path(), temp.path().join("turbo-index.sqlite3"))
                .expect("server")
                .enable_optional_tool(OPTIONAL_TURBOREPO)
                .expect("turbo");
        turbo_server.prepare().await.expect("prepare turbo");
        let packages = turbo_server
            .call(OPTIONAL_TURBOREPO, &json!({}))
            .expect("packages");''',
        label="Turborepo optional test server",
    )
    replace_exact(
        mcp,
        '        let routes = server.call(OPTIONAL_NEXTJS, &json!({})).expect("routes");',
        '''        let next_server =
            PublicMcpServer::with_store(temp.path(), temp.path().join("next-index.sqlite3"))
                .expect("server")
                .enable_optional_tool(OPTIONAL_NEXTJS)
                .expect("next");
        next_server.prepare().await.expect("prepare next");
        let routes = next_server
            .call(OPTIONAL_NEXTJS, &json!({}))
            .expect("routes");''',
        label="Next.js optional test server",
    )
    replace_exact(
        engine,
        "use soleaux_mcp::PublicMcpServer;",
        "use soleaux_mcp::{PUBLIC_ROOT_TOOL_MAX, PublicMcpServer};",
        label="engine MCP imports",
    )
    replace_exact(
        engine,
        '''    if count > 14 {
        bail!("public MCP profile exceeds the fourteen-tool hard cap");
    }''',
        '''    if count > PUBLIC_ROOT_TOOL_MAX {
        bail!("public MCP profile exceeds the twelve-tool hard cap");
    }''',
        label="doctor ceiling guard",
    )
    replace_exact(
        engine,
        "        public_root_tool_ceiling: 14,",
        "        public_root_tool_ceiling: PUBLIC_ROOT_TOOL_MAX,",
        label="doctor ceiling report",
    )

    result = {
        "status": "pass",
        "version": "0.4.0-dev.5",
        "hardCeiling": 12,
        "profileModule": str(profile.relative_to(root)),
        "productionClaimAllowed": False,
    }
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
