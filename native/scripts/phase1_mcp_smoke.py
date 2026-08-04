#!/usr/bin/env python3
"""Exact-profile and Context Packet V2 smoke for compiled Phase 1 binaries."""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
from typing import Any

import jsonschema

CANONICAL = [
    "context.compile",
    "code.search",
    "memory.search",
    "get_symbols",
    "registry.list",
    "registry.read",
    "repo_info",
    "navigate",
    "inspect",
    "preview",
    "edit",
    "restart_lsp",
]


class Mcp:
    def __init__(self, argv: list[str], env: dict[str, str]) -> None:
        self.process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        assert self.process.stdin is not None
        assert self.process.stdout is not None
        self.stdin = self.process.stdin
        self.stdout = self.process.stdout
        self.next_id = 1

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        request: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            request["params"] = params
        self.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
        self.stdin.flush()
        line = self.stdout.readline()
        if not line:
            stderr = self.process.stderr.read() if self.process.stderr is not None else ""
            raise AssertionError(f"MCP process stopped before response: {stderr}")
        response = json.loads(line)
        assert response.get("id") == request_id, response
        if "error" in response:
            raise AssertionError(response)
        return response["result"]

    def tool(self, name: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        result = self.call("tools/call", {"name": name, "arguments": arguments})
        return result["structuredContent"], bool(result["isError"])

    def close(self) -> None:
        self.stdin.close()
        return_code = self.process.wait(timeout=20)
        stderr = self.process.stderr.read() if self.process.stderr is not None else ""
        if return_code != 0:
            raise AssertionError(f"MCP process exited {return_code}: {stderr}")


def assert_envelope(value: dict[str, Any], *, source: str, error: bool = False) -> None:
    required = {
        "schema_version",
        "product_version",
        "request_id",
        "workspace_id",
        "snapshot_id",
        "workspace",
        "status",
        "data",
        "rows",
        "evidence",
        "coverage",
        "warnings",
        "next_cursor",
        "suggested_next_requests",
        "error",
        "source",
        "engine",
        "engine_version",
        "trust",
        "provenance",
        "cache_status",
        "truncated",
        "continuation_cursor",
        "sensitivity",
        "duration_us",
    }
    assert set(value) == required, (source, sorted(set(value) ^ required))
    assert value["schema_version"] == "soleaux.mcp/v2"
    assert value["product_version"] == "0.4.0-dev.5"
    assert value["source"] == source
    assert value["status"] == ("error" if error else "ok")
    assert value["error"] is not None if error else value["error"] is None
    assert value["data"] is None if error else value["data"] is not None
    assert value["provenance"]["provider"]
    assert value["provenance"]["engine"]
    assert value["provenance"]["engine_version"]
    assert value["provenance"]["range_encoding"] in {
        "utf8-bytes-zero-based",
        "utf16-lines-one-based",
        "none",
    }


def create_fixture(root: pathlib.Path) -> None:
    (root / "src").mkdir(parents=True)
    (root / ".github").mkdir()
    (root / ".agents" / "skills" / "review").mkdir(parents=True)
    (root / ".soleaux" / "team-memory").mkdir(parents=True)
    (root / "src" / "context.ts").write_text(
        "export function compileContext(task: string) {\n"
        "  const apiKey = 'sk-live-fixture-secret';\n"
        "  return { task, apiKey };\n"
        "}\n",
        encoding="utf-8",
    )
    (root / "src" / "consumer.ts").write_text(
        "import { compileContext } from './context';\n"
        "export const result = compileContext('phase1');\n",
        encoding="utf-8",
    )
    (root / ".github" / "CODEOWNERS").write_text("src/** @soleaux/core\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("# Constraints\nKeep public tools at twelve.\n", encoding="utf-8")
    (root / ".agents" / "skills" / "review" / "SKILL.md").write_text(
        "# Review\nReview compiled context provenance.\n", encoding="utf-8"
    )
    (root / ".soleaux" / "team-memory" / "decision.md").write_text(
        "Phase 1 uses the exact twelve-slot profile.\n", encoding="utf-8"
    )
    (root / "package.json").write_text(
        json.dumps({"name": "phase1-fixture", "scripts": {"test": "node --test", "lint": "eslint ."}}),
        encoding="utf-8",
    )


def base_smoke(binary: pathlib.Path, source: pathlib.Path, context_schema: dict[str, Any]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="soleaux-phase1-") as workspace, tempfile.TemporaryDirectory(
        prefix="soleaux-home-"
    ) as home:
        root = pathlib.Path(workspace)
        create_fixture(root)
        env = dict(os.environ)
        env["SOLEAUX_HOME"] = home
        mcp = Mcp([str(binary), "serve", str(root)], env)
        initialize = mcp.call("initialize", {"protocolVersion": "2026-07-28"})
        assert initialize["protocolVersion"] == "2026-07-28"
        tools = mcp.call("tools/list")["tools"]
        names = [tool["name"] for tool in tools]
        assert names == CANONICAL, names
        assert len(names) == 12

        context, is_error = mcp.tool(
            "context.compile",
            {
                "objective": "Update compileContext without leaking secrets and preserve ownership rules",
                "paths": ["src/context.ts"],
                "resource_uris": ["soleaux://about"],
                "token_budget": 4000,
                "max_bytes": 65536,
            },
        )
        assert not is_error
        assert_envelope(context, source="context.compile")
        jsonschema.Draft202012Validator(context_schema).validate(context["data"])
        packet = context["data"]
        assert packet["schema_version"] == "soleaux.context/v2"
        assert packet["native"]["selected_parsers_native"] is True
        assert packet["native"]["selected_lsps_native"] is True
        assert packet["canonical_owners"]
        assert packet["constraints"]
        assert packet["validation_routes"]
        assert packet["secret_redactions"] >= 1
        assert "sk-live-fixture-secret" not in json.dumps(packet)
        assert packet["coverage_complete"] is False or not packet["gaps"]

        search, is_error = mcp.tool("code.search", {"query": "compileContext", "limit": 20})
        assert not is_error
        assert_envelope(search, source="code.search")
        assert search["data"]["matches"]

        symbols, is_error = mcp.tool(
            "get_symbols", {"path": "src/context.ts", "include_source": True, "max_source_bytes_per_symbol": 4096}
        )
        assert not is_error
        assert_envelope(symbols, source="get_symbols")
        assert symbols["data"]["symbols"]

        registry, is_error = mcp.tool("registry.list", {"limit": 100})
        assert not is_error
        assert_envelope(registry, source="registry.list")
        domains = {item["name"] for item in registry["data"]["domains"]}
        assert {"tables", "ownership", "skills", "agents", "rules", "mcp_backends"} <= domains

        ownership, is_error = mcp.tool(
            "registry.read", {"tables": ["ownership", "frameworks"], "include_ownership": True, "limit": 50}
        )
        assert not is_error
        assert_envelope(ownership, source="registry.read")
        assert ownership["data"]["ownership"]

        memory, is_error = mcp.tool("memory.search", {"query": "twelve-slot", "scopes": ["team"]})
        assert not is_error
        assert_envelope(memory, source="memory.search")
        assert memory["data"]["attached"] is True
        assert memory["data"]["items"]

        info, is_error = mcp.tool("repo_info", {})
        assert not is_error
        assert_envelope(info, source="repo_info")
        assert info["data"]["active_tools"] == CANONICAL
        assert info["data"]["hard_ceiling"] == 12
        assert info["data"]["production_claim_allowed"] is False

        navigate, is_error = mcp.tool(
            "navigate",
            {"operation": "definition", "path": "src/context.ts", "line": 1, "column": 17},
        )
        assert not is_error
        assert_envelope(navigate, source="navigate")
        assert navigate["data"]["soft_deadline_ms"] == 800

        inspect, is_error = mcp.tool(
            "inspect",
            {"operation": "diagnostics", "path": "src/context.ts", "line": 1, "column": 1},
        )
        assert not is_error
        assert_envelope(inspect, source="inspect")
        assert inspect["data"]["soft_deadline_ms"] == 800

        target = root / "src" / "context.ts"
        before = target.read_text(encoding="utf-8")
        preview, is_error = mcp.tool(
            "preview",
            {
                "operation": "structural_rewrite",
                "paths": ["src/context.ts"],
                "structural": {"search": "return { task, apiKey };", "replacement": "return { task };"},
            },
        )
        assert not is_error
        assert_envelope(preview, source="preview")
        assert target.read_text(encoding="utf-8") == before
        assert preview["data"]["writes_performed"] is False

        edit, is_error = mcp.tool(
            "edit",
            {
                "preview_id": preview["data"]["preview_id"],
                "digest": preview["data"]["digest"],
                "confirm": True,
            },
        )
        assert not is_error
        assert_envelope(edit, source="edit")
        assert edit["data"]["applied"] is True
        assert "return { task };" in target.read_text(encoding="utf-8")

        restart, is_error = mcp.tool("restart_lsp", {})
        assert is_error
        assert_envelope(restart, source="restart_lsp", error=True)

        mcp.close()
        return {
            "canonical_tools": names,
            "context_returned_items": packet["returned_item_count"],
            "context_secret_redactions": packet["secret_redactions"],
            "context_coverage_complete": packet["coverage_complete"],
            "edit_receipt": edit["data"]["receipt_id"],
        }


def substitution_smoke(binary: pathlib.Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="soleaux-turbo-") as workspace, tempfile.TemporaryDirectory(
        prefix="soleaux-home-"
    ) as home:
        root = pathlib.Path(workspace)
        (root / "apps" / "web").mkdir(parents=True)
        (root / "packages" / "ui").mkdir(parents=True)
        (root / "package.json").write_text(
            json.dumps({"private": True, "workspaces": ["apps/*", "packages/*"]}), encoding="utf-8"
        )
        (root / "turbo.json").write_text(json.dumps({"tasks": {"build": {}}}), encoding="utf-8")
        (root / "apps" / "web" / "package.json").write_text(
            json.dumps({"name": "web", "dependencies": {"ui": "workspace:*"}}), encoding="utf-8"
        )
        (root / "packages" / "ui" / "package.json").write_text(json.dumps({"name": "ui"}), encoding="utf-8")
        env = dict(os.environ)
        env["SOLEAUX_HOME"] = home
        mcp = Mcp(
            [str(binary), "serve", str(root), "--substitute", "restart_lsp=turborepo.packages"], env
        )
        mcp.call("initialize", {"protocolVersion": "2025-11-25"})
        names = [tool["name"] for tool in mcp.call("tools/list")["tools"]]
        expected = [*CANONICAL[:-1], "turborepo.packages"]
        assert names == expected, names
        assert len(names) == 12
        turbo, is_error = mcp.tool("turborepo.packages", {"context_path": "apps/web/src/index.ts"})
        assert not is_error
        assert_envelope(turbo, source="turborepo.packages")
        assert len(turbo["data"]["packages"]) == 2
        mcp.close()
        return {"substituted_tools": names, "package_count": len(turbo["data"]["packages"])}


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit("usage: phase1_mcp_smoke.py <soleauxd> <source-root> <output-json>")
    binary = pathlib.Path(sys.argv[1]).resolve()
    source = pathlib.Path(sys.argv[2]).resolve()
    output = pathlib.Path(sys.argv[3]).resolve()
    context_schema = json.loads((source / "contracts" / "context-packet-v2.schema.json").read_text())
    result = {
        "schema_version": "soleaux.phase1.mcp-smoke/v1",
        "product_version": "0.4.0-dev.5",
        "production_claim_allowed": False,
        "base": base_smoke(binary, source, context_schema),
        "substitution": substitution_smoke(binary),
        "status": "pass",
    }
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
