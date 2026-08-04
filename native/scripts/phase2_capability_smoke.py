#!/usr/bin/env python3
"""Native Phase 2 gateway, catalog, provisioning, governance, and provider smoke."""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
from typing import Any

import jsonschema

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from phase1_mcp_smoke import CANONICAL, Mcp, assert_envelope  # noqa: E402


def run_json(argv: list[str], env: dict[str, str], stdin: str | None = None) -> Any:
    completed = subprocess.run(
        argv,
        input=stdin,
        text=True,
        capture_output=True,
        env=env,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"command failed ({completed.returncode}): {' '.join(argv)}\n"
            f"stdout={completed.stdout}\nstderr={completed.stderr}"
        )
    return json.loads(completed.stdout)


def create_backend(path: pathlib.Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
import json, os, sys
for line in sys.stdin:
    request=json.loads(line)
    if 'id' not in request:
        continue
    method=request.get('method')
    if method == 'initialize':
        result={'protocolVersion':'2025-11-25','capabilities':{'tools':{}},'serverInfo':{'name':'fixture','version':'1'}}
    elif method == 'tools/call':
        params=request.get('params',{})
        result={'content':[{'type':'text','text':'ok'}],'structuredContent':{'tool':params.get('name'),'arguments':params.get('arguments',{}),'token_present':bool(os.environ.get('SOLEAUX_GATEWAY_TOKEN'))}}
    else:
        result={}
    print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':result}), flush=True)
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def gateway_and_provisioning(cli: pathlib.Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="soleaux-phase2-workspace-") as workspace, tempfile.TemporaryDirectory(
        prefix="soleaux-phase2-home-"
    ) as home, tempfile.TemporaryDirectory(prefix="soleaux-phase2-team-") as team:
        root = pathlib.Path(workspace)
        home_path = pathlib.Path(home)
        team_path = pathlib.Path(team)
        backend = root / "fixture_backend.py"
        create_backend(backend)
        (root / "src").mkdir()
        (root / "src" / "lib.ts").write_text("export const value = 1;\n", encoding="utf-8")
        (root / ".github").mkdir()
        (root / ".github" / "CODEOWNERS").write_text("src/** @team/core\n", encoding="utf-8")
        (root / "AGENTS.md").write_text("# Rules\nRun tests before merge.\n", encoding="utf-8")
        (root / "package.json").write_text(
            json.dumps({"name": "phase2", "scripts": {"test": "node --test", "lint": "eslint ."}}),
            encoding="utf-8",
        )
        (root / "soleaux.toml").write_text(
            "[mcp.fixture]\n"
            f"command = [{json.dumps(sys.executable)}, {json.dumps(str(backend))}]\n"
            'namespace = "team.fixture"\n'
            'auth = "oauth"\n'
            'oauth_scopes = ["read"]\n',
            encoding="utf-8",
        )

        (home_path / "catalog" / "skills" / "review").mkdir(parents=True)
        (home_path / "catalog" / "skills" / "review" / "SKILL.md").write_text(
            "# Review\nUser review skill.\n", encoding="utf-8"
        )
        (team_path / "agents").mkdir(parents=True)
        (team_path / "agents" / "release.md").write_text("# Release agent\n", encoding="utf-8")
        (team_path / "rules").mkdir(parents=True)
        (team_path / "rules" / "quality.md").write_text("# Quality rule\n", encoding="utf-8")

        env = dict(os.environ)
        env["SOLEAUX_HOME"] = str(home_path)
        env["SOLEAUX_TEAM_CATALOG"] = str(team_path)
        env["SOLEAUXD"] = str(cli.with_name("soleauxd"))

        status_before = run_json([str(cli), "mcp", "status", str(root)], env)
        assert len(status_before) == 1
        assert status_before[0]["backend"]["namespace"] == "team.fixture"
        assert status_before[0]["authenticated"] is False
        assert status_before[0]["root_tool_inflation"] is False

        login = run_json(
            [str(cli), "mcp", "login", "fixture", "--token-stdin"], env, stdin="fixture-token\n"
        )
        credential = pathlib.Path(login["credential_store"])
        assert credential.is_file()
        assert not credential.is_relative_to(root)
        assert login["worktree_write"] is False

        invocation = run_json(
            [
                str(cli),
                "mcp",
                "call",
                "fixture",
                "echo",
                "--arguments",
                '{"value":42}',
                str(root),
            ],
            env,
        )
        structured = invocation["response"]["structuredContent"]
        assert structured["tool"] == "echo"
        assert structured["arguments"] == {"value": 42}
        assert structured["token_present"] is True

        catalog = run_json([str(cli), "catalog", "list", str(root), "--limit", "200"], env)
        assert_envelope(catalog, source="registry.list")
        entries = catalog["data"]["entries"]
        scopes = {
            entry.get("metadata", {}).get("scope")
            for entry in entries
            if isinstance(entry, dict)
        }
        # Compact listing does not include metadata; inspect each domain through MCP below.
        assert len(catalog["data"]["domains"]) >= 6

        plan = run_json([str(cli), "adopt", str(root), "--dry-run"], env)
        assert plan["public_tool_ceiling"] == 12
        assert plan["root_tool_inflation"] is False
        adopted = run_json([str(cli), "adopt", str(root), "--yes"], env)
        assert adopted["root_tool_inflation"] is False
        assert (root / ".mcp.json").is_file()
        assert "soleaux:managed:begin" in (root / "AGENTS.md").read_text(encoding="utf-8")
        restored = run_json([str(cli), "adopt", str(root), "--revert"], env)
        assert restored["restored"]
        assert "soleaux:managed:begin" not in (root / "AGENTS.md").read_text(encoding="utf-8")

        attach_plan = run_json([str(cli), "attach", str(root), "--dry-run"], env)
        assert attach_plan["public_tool_ceiling"] == 12
        attached = run_json([str(cli), "attach", str(root), "--yes"], env)
        assert attached["root_tool_inflation"] is False
        assert (root / ".soleaux" / "attachment.json").is_file()
        assert list((home_path / "workspaces").glob("*.json"))

        logout = run_json([str(cli), "mcp", "logout", "fixture"], env)
        assert logout["status"] == "removed"
        assert not credential.exists()

        return {
            "gateway_namespace": status_before[0]["backend"]["namespace"],
            "gateway_invoked": structured["tool"],
            "credential_outside_worktree": True,
            "catalog_domain_count": len(catalog["data"]["domains"]),
            "adopt_written": len(adopted["written"]),
            "attach_written": len(attached["written"]),
            "catalog_scopes_placeholder": sorted(value for value in scopes if value),
        }


def mcp_phase2(binary: pathlib.Path, source: pathlib.Path) -> dict[str, Any]:
    schema = json.loads((source / "contracts" / "context-packet-v2.schema.json").read_text())
    with tempfile.TemporaryDirectory(prefix="soleaux-phase2-mcp-") as workspace, tempfile.TemporaryDirectory(
        prefix="soleaux-phase2-home-"
    ) as home, tempfile.TemporaryDirectory(prefix="soleaux-phase2-team-") as team:
        root = pathlib.Path(workspace)
        home_path = pathlib.Path(home)
        team_path = pathlib.Path(team)
        (root / "src").mkdir()
        (root / ".github").mkdir()
        (root / ".soleaux" / "catalog" / "rules").mkdir(parents=True)
        (root / "src" / "service.ts").write_text(
            "export function runService() { return 'ok'; }\n", encoding="utf-8"
        )
        (root / ".github" / "CODEOWNERS").write_text("src/** @team/core\n", encoding="utf-8")
        (root / "AGENTS.md").write_text("# Constraints\nRun tests before merge.\n", encoding="utf-8")
        (root / "package.json").write_text(
            json.dumps({"name": "phase2", "scripts": {"test": "node --test", "lint": "eslint ."}}),
            encoding="utf-8",
        )
        (root / ".soleaux" / "catalog" / "rules" / "security.md").write_text(
            "# Security\nDo not leak tokens.\n", encoding="utf-8"
        )
        (home_path / "catalog" / "skills" / "native").mkdir(parents=True)
        (home_path / "catalog" / "skills" / "native" / "SKILL.md").write_text(
            "# Native skill\nUse native intelligence.\n", encoding="utf-8"
        )
        (team_path / "agents").mkdir(parents=True)
        (team_path / "agents" / "review.md").write_text("# Review agent\n", encoding="utf-8")
        (root / "soleaux.toml").write_text(
            '[mcp.docs]\nurl = "http://127.0.0.1:49999/mcp"\nnamespace = "team.docs"\nauth = "none"\n',
            encoding="utf-8",
        )
        env = dict(os.environ)
        env["SOLEAUX_HOME"] = str(home_path)
        env["SOLEAUX_TEAM_CATALOG"] = str(team_path)
        mcp = Mcp([str(binary), "serve", str(root)], env)
        mcp.call("initialize", {"protocolVersion": "2026-07-28"})
        names = [tool["name"] for tool in mcp.call("tools/list")["tools"]]
        assert names == CANONICAL
        assert len(names) == 12

        context, is_error = mcp.tool(
            "context.compile",
            {"objective": "Update runService while respecting ownership and validation", "paths": ["src/service.ts"]},
        )
        assert not is_error
        assert_envelope(context, source="context.compile")
        jsonschema.Draft202012Validator(schema).validate(context["data"])
        packet = context["data"]
        assert packet["canonical_owners"]
        assert packet["constraints"]
        assert packet["validation_routes"]
        assert any(
            item["provenance"]["provider"] == "soleaux-native-governance-graph"
            for section in ("constraints", "validation_routes")
            for item in packet[section]
        )

        listed, is_error = mcp.tool("registry.list", {"limit": 200})
        assert not is_error
        assert_envelope(listed, source="registry.list")
        domains = {domain["name"] for domain in listed["data"]["domains"]}
        assert {"skills", "agents", "rules", "mcp_backends"} <= domains

        for domain, expected_scope in [("skills", "user"), ("agents", "team"), ("rules", "workspace")]:
            ids = [entry["id"] for entry in listed["data"]["entries"] if entry["domain"] == domain]
            assert ids, (domain, listed)
            read, is_error = mcp.tool("registry.read", {"domain": domain, "ids": ids, "limit": 200})
            assert not is_error
            assert_envelope(read, source="registry.read")
            assert any(entry["metadata"].get("scope") == expected_scope for entry in read["data"]["entries"])

        governance, is_error = mcp.tool("registry.read", {"tables": ["governance"], "limit": 200})
        assert not is_error
        assert_envelope(governance, source="registry.read")
        rows = governance["data"]["tables"]["governance"]["rows"]
        assert any(row["kind"] == "owns" for row in rows)
        assert any(row["kind"] == "constrains" for row in rows)
        assert any(row["kind"] == "validates" for row in rows)

        info, is_error = mcp.tool("repo_info", {})
        assert not is_error
        assert_envelope(info, source="repo_info")
        assert info["data"]["gateway"]["root_tool_inflation"] is False
        assert info["data"]["gateway"]["backends"][0]["backend"]["namespace"] == "team.docs"
        assert info["data"]["governance"]["edge_count"] >= 3
        assert info["data"]["active_tools"] == CANONICAL
        mcp.close()
        return {
            "canonical_tools": names,
            "governance_edges": len(rows),
            "catalog_domains": sorted(domains),
            "context_schema": packet["schema_version"],
        }


def optional_providers(binary: pathlib.Path) -> dict[str, Any]:
    results: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="soleaux-phase2-postgres-") as workspace, tempfile.TemporaryDirectory(
        prefix="soleaux-phase2-home-"
    ) as home:
        root = pathlib.Path(workspace)
        (root / "README.md").write_text("postgres fixture\n", encoding="utf-8")
        env = dict(os.environ)
        env["SOLEAUX_HOME"] = home
        mcp = Mcp(
            [str(binary), "serve", str(root), "--substitute", "restart_lsp=parse_and_validate_postgres_sql"],
            env,
        )
        mcp.call("initialize", {"protocolVersion": "2025-11-25"})
        names = [tool["name"] for tool in mcp.call("tools/list")["tools"]]
        assert len(names) == 12 and names[-1] == "parse_and_validate_postgres_sql"
        sql, is_error = mcp.tool(
            "parse_and_validate_postgres_sql",
            {"sql": "select u.id from public.users u join public.accounts a on a.user_id=u.id"},
        )
        assert not is_error
        assert_envelope(sql, source="parse_and_validate_postgres_sql")
        assert {"users", "accounts"} <= {name.split(".")[-1] for name in sql["data"]["relations"]}
        results["postgres_tools"] = names
        mcp.close()

    with tempfile.TemporaryDirectory(prefix="soleaux-phase2-next-") as workspace, tempfile.TemporaryDirectory(
        prefix="soleaux-phase2-home-"
    ) as home:
        root = pathlib.Path(workspace)
        (root / "app" / "users" / "[id]").mkdir(parents=True)
        (root / "app" / "api" / "health").mkdir(parents=True)
        (root / "next.config.mjs").write_text("export default {};\n", encoding="utf-8")
        (root / "package.json").write_text(json.dumps({"dependencies": {"next": "16.0.0"}}), encoding="utf-8")
        (root / "app" / "users" / "[id]" / "page.tsx").write_text("export default function Page(){return null}\n", encoding="utf-8")
        (root / "app" / "api" / "health" / "route.ts").write_text("export async function GET(){return new Response('ok')}\n", encoding="utf-8")
        env = dict(os.environ)
        env["SOLEAUX_HOME"] = home
        mcp = Mcp(
            [str(binary), "serve", str(root), "--substitute", "restart_lsp=next.get_routes"], env
        )
        mcp.call("initialize", {"protocolVersion": "2025-11-25"})
        names = [tool["name"] for tool in mcp.call("tools/list")["tools"]]
        assert len(names) == 12 and names[-1] == "next.get_routes"
        routes, is_error = mcp.tool("next.get_routes", {})
        assert not is_error
        assert_envelope(routes, source="next.get_routes")
        paths = {route["route"] for route in routes["data"]["routes"]}
        assert "/users/:id" in paths
        assert "/api/health" in paths
        assert routes["data"]["runtime"]["capability_driven"] is True
        results["next_tools"] = names
        results["next_routes"] = sorted(paths)
        mcp.close()
    return results


def main() -> None:
    if len(sys.argv) != 5:
        raise SystemExit(
            "usage: phase2_capability_smoke.py <soleaux> <soleauxd> <source-root> <output-json>"
        )
    cli = pathlib.Path(sys.argv[1]).resolve()
    daemon = pathlib.Path(sys.argv[2]).resolve()
    source = pathlib.Path(sys.argv[3]).resolve()
    output = pathlib.Path(sys.argv[4]).resolve()
    result = {
        "schema_version": "soleaux.phase2.capability-smoke/v1",
        "product_version": "0.4.0-dev.5",
        "production_claim_allowed": False,
        "phase3_started": False,
        "gateway_and_provisioning": gateway_and_provisioning(cli),
        "mcp": mcp_phase2(daemon, source),
        "optional_providers": optional_providers(daemon),
        "status": "pass",
    }
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
