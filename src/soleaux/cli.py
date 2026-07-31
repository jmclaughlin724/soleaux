"""Thin command-line adapters over SoleauxService."""

from __future__ import annotations

import argparse
import asyncio
import collections.abc
import json
import pathlib
import sys
import typing

import soleaux.analysis.service
import soleaux.contracts.requests
import soleaux.contracts.results


def _add_common_request_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace-id")
    parser.add_argument(
        "--semantic-mode",
        choices=[mode.value for mode in soleaux.contracts.requests.SemanticMode],
        default=soleaux.contracts.requests.SemanticMode.BEST_AVAILABLE.value,
    )
    parser.add_argument("--json", action="store_true")


def create_parser() -> argparse.ArgumentParser:
    """Build the canonical Soleaux command-line parser."""
    parser = argparse.ArgumentParser(prog="soleaux")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--root", type=pathlib.Path)
    subparsers = parser.add_subparsers(dest="command")

    describe = subparsers.add_parser("describe")
    _add_common_request_options(describe)

    search = subparsers.add_parser("search")
    search.add_argument("query")
    search.add_argument(
        "--kind",
        action="append",
        dest="kinds",
        choices=[kind.value for kind in soleaux.contracts.requests.SearchKind],
        default=[],
    )
    search.add_argument("--path", action="append", dest="paths", default=[])
    search.add_argument("--context-lines", type=int, default=2)
    search.add_argument("--cursor")
    search.add_argument("--limit", type=int, default=20)
    _add_common_request_options(search)

    context = subparsers.add_parser("context")
    context.add_argument("objective")
    context.add_argument("--path", action="append", dest="paths", default=[])
    context.add_argument("--max-bytes", type=int, default=32768)
    context.add_argument("--limit", type=int, default=50)
    _add_common_request_options(context)

    query = subparsers.add_parser("query")
    query.add_argument("--table", action="append", dest="tables", required=True)
    query.add_argument("--exclude-table", action="append", dest="exclude_tables", default=[])
    query.add_argument("--seed-key", action="append", dest="seed_keys", default=[])
    query.add_argument("--cursor")
    query.add_argument("--limit", type=int, default=50)
    _add_common_request_options(query)

    navigate = subparsers.add_parser("navigate")
    navigate.add_argument(
        "operation",
        choices=[operation.value for operation in soleaux.contracts.requests.SemanticOperation],
    )
    navigate.add_argument("--path")
    navigate.add_argument("--line", type=int)
    navigate.add_argument("--column", type=int)
    navigate.add_argument("--symbol-name")
    navigate.add_argument("--symbol-kind")
    navigate.add_argument("--limit", type=int, default=50)
    _add_common_request_options(navigate)

    inspect = subparsers.add_parser("inspect")
    inspect.add_argument(
        "operation",
        choices=[operation.value for operation in soleaux.contracts.requests.InspectOperation],
    )
    inspect.add_argument("path")
    inspect.add_argument("--line", type=int, required=True)
    inspect.add_argument("--column", type=int, required=True)
    inspect.add_argument("--limit", type=int, default=50)
    _add_common_request_options(inspect)

    doctor = subparsers.add_parser("doctor")
    doctor.add_argument("--probe", action="store_true")
    doctor.add_argument("--json", action="store_true")

    benchmark = subparsers.add_parser("benchmark")
    benchmark.add_argument("--json", action="store_true")

    lint = subparsers.add_parser(
        "lint",
        help="Run the workspace's configured structural rules and print the envelope.",
    )
    lint.add_argument(
        "--path",
        action="append",
        dest="paths",
        default=[],
        help="Repository-relative file or directory scope (repeatable)",
    )
    lint.add_argument(
        "--rule",
        action="append",
        dest="rule_ids",
        default=[],
        help="Restrict to one configured rule id (repeatable)",
    )
    lint.add_argument(
        "--severity",
        action="append",
        dest="severities",
        default=[],
        help="Restrict to one severity such as error or warning (repeatable)",
    )
    lint.add_argument("--limit", type=int, default=100)
    lint.add_argument("--workspace-id")

    check = subparsers.add_parser("check")
    check_sub = check.add_subparsers(dest="check_target", required=True)
    check_mcp = check_sub.add_parser("mcp")
    check_mcp.add_argument("--json", action="store_true")
    check_mcp.add_argument(
        "--probe", action="store_true", help="Connect to each enabled backend and verify liveness"
    )
    check_health = check_sub.add_parser("health")
    check_health.add_argument("--json", action="store_true")

    suggest = subparsers.add_parser("suggest")
    suggest.add_argument("--json", action="store_true")

    install = subparsers.add_parser("install")
    install.add_argument(
        "name",
        help=(
            "Built-in provider or managed runtime "
            "(typescript-runtime, postgresql-parser, ast-grep-rust)"
        ),
    )

    generate = subparsers.add_parser("generate")
    generate_sub = generate.add_subparsers(dest="generate_target", required=True)
    gen_toml = generate_sub.add_parser("soleaux-toml")
    gen_toml.add_argument("--output", type=pathlib.Path, default=pathlib.Path("soleaux.toml"))

    adopt = subparsers.add_parser(
        "adopt",
        help="Detect competing language servers and adopt them under soleaux.",
    )
    adopt.add_argument("--dry-run", action="store_true", help="Print the plan without writing.")
    adopt.add_argument("--yes", action="store_true", help="Skip the interactive confirmation.")
    adopt.add_argument("--revert", action="store_true", help="Restore the most recent backups.")
    adopt.add_argument(
        "--target",
        action="append",
        choices=["editor", "mcp", "providers"],
        help="Scope writes (repeatable). Default: all three.",
    )
    adopt.add_argument(
        "--language",
        action="append",
        help="Restrict detection to a language (repeatable, e.g., python, typescript).",
    )
    adopt.add_argument(
        "--force",
        action="store_true",
        help="Overwrite a healthy existing soleaux registration.",
    )

    return parser


async def run_cli(
    argv: collections.abc.Sequence[str],
    *,
    service: soleaux.analysis.service.SoleauxService | None = None,
    stdout: typing.TextIO | None = None,
) -> int:
    """Execute one non-stdio CLI adapter and serialize its service envelope."""
    args = create_parser().parse_args(list(argv))
    output = stdout or sys.stdout
    if args.version:
        output.write(f"soleaux {soleaux.analysis.service.product_version()}\n")
        return 0
    if args.command is None:
        raise ValueError("run_cli does not own the stdio transport")

    root = args.root or pathlib.Path.cwd()
    if args.command == "check":
        if args.check_target == "mcp":
            exit_code = _check_mcp(
                root, json_output=bool(getattr(args, "json", False)), stdout=output
            )
            if getattr(args, "probe", False):
                exit_code = await _probe_mcp_backends(
                    root, json_output=bool(getattr(args, "json", False)), stdout=output
                )
            return exit_code
        if args.check_target == "health":
            return _check_health(
                root, json_output=bool(getattr(args, "json", False)), stdout=output
            )
    if args.command == "suggest":
        return _run_suggest(root, json_output=bool(getattr(args, "json", False)), stdout=output)
    if args.command == "install":
        if args.name == "typescript-runtime":
            from soleaux.typescript.node_runtime import (
                TypeScriptRuntimeError,
                provision_typescript_runtime,
            )

            try:
                installation = provision_typescript_runtime()
            except TypeScriptRuntimeError as exc:
                output.write(f"[FAIL] typescript-runtime: {exc}\n")
                return 1
            output.write(
                "[OK] typescript-runtime: "
                f"ts-morph {installation.ts_morph_version}, "
                f"native TypeScript {installation.native_version} "
                f"at {installation.prefix}\n"
            )
            return 0
        if args.name == "postgresql-parser":
            from soleaux.postgresql.node_runtime import (
                NodeParserError,
                provision_parser,
            )

            try:
                installation = provision_parser()
            except NodeParserError as exc:
                output.write(f"[FAIL] postgresql-parser: {exc}\n")
                return 1
            output.write(
                f"[OK] postgresql-parser: {installation.version} at {installation.prefix}\n"
            )
            return 0
        if args.name == "ast-grep-rust":
            from soleaux.structural.rust_runtime import RustWorkerError, provision_rust_worker

            try:
                worker = provision_rust_worker()
            except RustWorkerError as exc:
                output.write(f"[FAIL] ast-grep-rust: {exc}\n")
                return 1
            output.write(f"[OK] ast-grep-rust: {worker.version} at {worker.binary_path}\n")
            return 0
        from soleaux.lsp.install import install_provider

        result = install_provider(args.name, root)
        output.write(f"[{'OK' if result.success else 'FAIL'}] {result.name}: {result.message}\n")
        return 0 if result.success else 1
    if args.command == "generate" and args.generate_target == "soleaux-toml":
        return _generate_soleaux_toml(
            root, getattr(args, "output", pathlib.Path("soleaux.toml")), stdout=output
        )
    if args.command == "adopt":
        return _run_adopt(args, root, stdout=output, stderr=sys.stderr)

    owns_service = service is None
    active = service or soleaux.analysis.service.SoleauxService.from_directory(
        args.root or pathlib.Path.cwd()
    )
    try:
        if args.command != "describe":
            await active.start()
        envelope = await _dispatch(active, args)
        output.write(json.dumps(envelope.model_dump(mode="json"), sort_keys=True))
        output.write("\n")
        if envelope.status is not soleaux.contracts.results.ResultStatus.OK:
            return 2 if args.command == "lint" else 1
        if args.command == "lint" and envelope.rows:
            return 1
        return 0
    finally:
        if owns_service:
            await active.aclose()


async def _dispatch(
    service: soleaux.analysis.service.SoleauxService,
    args: argparse.Namespace,
) -> soleaux.contracts.results.ResponseEnvelope | soleaux.contracts.results.TaskContextEnvelope:
    command = str(args.command)
    semantic_mode = soleaux.contracts.requests.SemanticMode(
        getattr(args, "semantic_mode", soleaux.contracts.requests.SemanticMode.BEST_AVAILABLE.value)
    )
    workspace_id = getattr(args, "workspace_id", None)
    if command == "describe":
        return await service.describe(
            soleaux.contracts.requests.DescribeRequest(
                workspace_id=workspace_id, semantic_mode=semantic_mode
            )
        )
    if command == "search":
        return await service.search(
            soleaux.contracts.requests.SearchRequest(
                workspace_id=workspace_id,
                semantic_mode=semantic_mode,
                query=args.query,
                kinds=args.kinds,
                paths=args.paths,
                context_lines=args.context_lines,
                cursor=args.cursor,
                limit=args.limit,
            )
        )
    if command == "context":
        return await service.context(
            soleaux.contracts.requests.ContextRequest(
                workspace_id=workspace_id,
                semantic_mode=semantic_mode,
                objective=args.objective,
                paths=args.paths,
                max_bytes=args.max_bytes,
                limit=args.limit,
            )
        )
    if command == "query":
        return await service.query(
            soleaux.contracts.requests.QueryRequest(
                workspace_id=workspace_id,
                semantic_mode=semantic_mode,
                include_tables=args.tables,
                exclude_tables=args.exclude_tables,
                seed_keys=args.seed_keys,
                cursor=args.cursor,
                limit=args.limit,
            )
        )
    if command == "navigate":
        return await service.navigate(
            soleaux.contracts.requests.NavigateRequest(
                workspace_id=workspace_id,
                semantic_mode=semantic_mode,
                operation=args.operation,
                path=args.path,
                line=args.line,
                column=args.column,
                symbol_name=args.symbol_name,
                symbol_kind=args.symbol_kind,
                limit=args.limit,
            )
        )
    if command == "inspect":
        return await service.inspect(
            soleaux.contracts.requests.InspectRequest(
                workspace_id=workspace_id,
                semantic_mode=semantic_mode,
                operation=args.operation,
                path=args.path,
                line=args.line,
                column=args.column,
                limit=args.limit,
            )
        )
    if command == "doctor":
        return await service.doctor(probe=bool(args.probe))
    if command == "benchmark":
        return await service.benchmark()
    if command == "lint":
        return await service.lint(
            soleaux.contracts.requests.LintRequest(
                workspace_id=workspace_id,
                paths=args.paths,
                rule_ids=args.rule_ids,
                severities=args.severities,
                limit=args.limit,
            )
        )
    raise ValueError(f"unsupported command: {command}")


def _check_mcp(root: pathlib.Path, *, json_output: bool, stdout: typing.TextIO) -> int:
    """Validate MCP server consistency across .mcp.json and .codex/config.toml."""
    import json as json_mod
    import tomllib

    findings: list[dict[str, str]] = []

    mcp_json_path = root / ".mcp.json"
    codex_config_path = root / ".codex" / "config.toml"

    mcp_servers: set[str] = set()
    if mcp_json_path.is_file():
        try:
            mcp_data = json_mod.loads(mcp_json_path.read_text(encoding="utf-8"))
            mcp_servers = set(mcp_data.get("mcpServers", {}).keys())
        except json_mod.JSONDecodeError, KeyError:
            findings.append({"severity": "error", "finding": ".mcp.json is invalid JSON"})

    codex_servers: set[str] = set()
    codex_disabled: set[str] = set()
    if codex_config_path.is_file():
        try:
            tomldata = tomllib.loads(codex_config_path.read_text(encoding="utf-8"))
            for name, cfg in tomldata.get("mcp_servers", {}).items():
                codex_servers.add(name)
                if not cfg.get("enabled", True):
                    codex_disabled.add(name)
        except tomllib.TOMLDecodeError:
            findings.append({"severity": "error", "finding": ".codex/config.toml is invalid TOML"})

    only_mcp = mcp_servers - codex_servers
    only_codex = codex_servers - mcp_servers
    for name in sorted(only_mcp):
        findings.append(
            {"severity": "warning", "finding": f"{name} in .mcp.json but not .codex/config.toml"}
        )
    for name in sorted(only_codex):
        findings.append(
            {"severity": "warning", "finding": f"{name} in .codex/config.toml but not .mcp.json"}
        )
    for name in sorted(codex_disabled):
        findings.append(
            {"severity": "info", "finding": f"{name} is disabled — consider removing dead config"}
        )

    if json_output:
        stdout.write(json_mod.dumps(findings, indent=2) + "\n")
    else:
        if not findings:
            stdout.write(f"MCP configs are consistent ({len(mcp_servers)} servers)\n")
        else:
            for f in findings:
                stdout.write(f"[{f['severity']}] {f['finding']}\n")
    return 1 if any(f["severity"] == "error" for f in findings) else 0


def _check_health(root: pathlib.Path, *, json_output: bool, stdout: typing.TextIO) -> int:
    """Scan workspace .tmp/ against health thresholds from soleaux.toml."""
    import json as json_mod

    from soleaux.contracts.config import load_config

    config = load_config(root)
    thresholds = config.health

    tmp_path = root / ".tmp"
    tmp_entries = 0
    if tmp_path.is_dir():
        tmp_entries = sum(1 for _ in tmp_path.iterdir())

    findings: list[dict[str, str]] = []
    if tmp_entries > 100:
        findings.append(
            {
                "severity": "warning",
                "finding": f".tmp/ has {tmp_entries} entries (threshold: 100)",
            }
        )

    payload = {
        "thresholds": thresholds.model_dump(mode="json"),
        "tmp_entries": tmp_entries,
        "findings": findings,
    }

    if json_output:
        stdout.write(json_mod.dumps(payload, indent=2) + "\n")
    else:
        stdout.write(f"Health thresholds: {thresholds.model_dump(mode='json')}\n")
        stdout.write(f".tmp/ entries: {tmp_entries}\n")
        for f in findings:
            stdout.write(f"[{f['severity']}] {f['finding']}\n")
    return 0


def _generate_soleaux_toml(root: pathlib.Path, output: pathlib.Path, stdout: typing.TextIO) -> int:
    """Generate a soleaux.toml template from existing workspace configs."""
    import json as json_mod
    import tomllib

    lines: list[str] = [
        "# Generated by soleaux from existing workspace configuration.",
        "# Edit and place at the workspace root.",
        "",
    ]

    mcp_json_path = root / ".mcp.json"
    if mcp_json_path.is_file():
        try:
            mcp_data = json_mod.loads(mcp_json_path.read_text(encoding="utf-8"))
            servers = sorted(mcp_data.get("mcpServers", {}).keys())
            if servers:
                lines.append("# [mcp.<name>] entries derived from .mcp.json")
                lines.append("# Enable one per backend as needed:")
                lines.append(f"# servers found: {', '.join(servers)}")
                lines.append("")
        except json_mod.JSONDecodeError:
            pass

    codex_path = root / ".codex" / "config.toml"
    if codex_path.is_file():
        try:
            tomldata = tomllib.loads(codex_path.read_text(encoding="utf-8"))
            for name, cfg in tomldata.get("mcp_servers", {}).items():
                if cfg.get("enabled", True) and "command" in cfg:
                    cmd = cfg["command"]
                    lines.append(f'[mcp."{name}"]')
                    lines.append(f"command = {json_mod.dumps(cmd)}")
                    lines.append('lifecycle = "session"')
                    lines.append("")
        except tomllib.TOMLDecodeError:
            pass

    lines.append("[health]")
    lines.append("logs_retention_days = 7")
    lines.append("temp_retention_hours = 24")
    lines.append("archived_sessions_retention_days = 14")
    lines.append("max_logs_db_size_mb = 500")
    lines.append("")

    from soleaux.suggestions import scan_for_suggestions

    configured_names: set[str] = set()
    for line in lines:
        if line.startswith('[mcp."') or line.startswith("[mcp."):
            name = line.split('"')[1] if '"' in line else line.split("[mcp.")[1].rstrip("]")
            configured_names.add(name)
    suggestions = scan_for_suggestions(root)
    unconfigured = [s for s in suggestions if s.name not in configured_names]
    if unconfigured:
        lines.append("# Suggested MCP servers detected in this workspace:")
        for s in unconfigured:
            lines.append(f"# {s.name}: {s.rationale}")
            if s.command:
                lines.append(f'# [mcp."{s.name}"]')
                lines.append(f"# command = {json_mod.dumps(s.command)}")
                lines.append('# lifecycle = "session"')
            elif s.url:
                lines.append(f'# [mcp."{s.name}"]')
                lines.append(f'# url = "{s.url}"')
                if s.auth_token_env_hint:
                    lines.append(f'# auth_token_env = "{s.auth_token_env_hint}"')
            lines.append("#")
        lines.append("")

    content = "\n".join(lines)
    if output == pathlib.Path("-"):
        stdout.write(content)
    else:
        output.write_text(content, encoding="utf-8")
        stdout.write(f"Written to {output}\n")
    return 0


def _run_suggest(root: pathlib.Path, *, json_output: bool, stdout: typing.TextIO) -> int:
    """Report MCP server suggestions matched against workspace signals."""
    import json as json_mod

    from soleaux.contracts.config import load_config
    from soleaux.suggestions import scan_for_suggestions

    matched = scan_for_suggestions(root)
    config = load_config(root)
    configured: set[str] = set(config.mcp.keys()) if config.mcp else set()

    results = [
        {
            "name": s.name,
            "rationale": s.rationale,
            "configured": s.name in configured,
        }
        for s in matched
    ]

    if json_output:
        stdout.write(json_mod.dumps(results, indent=2) + "\n")
    elif not results:
        stdout.write("No MCP server suggestions for this workspace.\n")
    else:
        for r in results:
            status = "configured" if r["configured"] else "suggested"
            stdout.write(f"[{status}] {r['name']}: {r['rationale']}\n")
    return 0


async def _probe_mcp_backends(
    root: pathlib.Path, *, json_output: bool, stdout: typing.TextIO
) -> int:
    """Connect to each enabled [mcp.*] backend and verify liveness."""
    import json as json_mod
    import time

    from fastmcp import Client

    from soleaux.contracts.config import load_config
    from soleaux.gateway import _transport_factory

    config = load_config(root)
    if not config.mcp:
        if json_output:
            stdout.write("[]\n")
        else:
            stdout.write("No MCP backends configured.\n")
        return 0

    results: list[dict[str, object]] = []
    for name, backend in sorted(config.mcp.items()):
        if not backend.enabled:
            continue
        entry: dict[str, object] = {"name": name}
        try:
            if backend.command is None and backend.url is None:
                entry["alive"] = False
                entry["error"] = "no command or url"
                results.append(entry)
                continue
            transport = _transport_factory(backend, root)()
            init_timeout = backend.init_timeout_seconds
            req_timeout = backend.request_timeout_seconds
            started = time.perf_counter()
            async with Client(transport, init_timeout=init_timeout, timeout=req_timeout) as client:
                tools = await client.list_tools()
                entry["alive"] = True
                entry["tool_count"] = len(tools)
                entry["tool_names"] = [t.name for t in tools[:5]]
                entry["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
        except Exception as exc:
            entry["alive"] = False
            entry["error"] = str(exc)[:200]
        results.append(entry)

    if json_output:
        stdout.write(json_mod.dumps(results, indent=2) + "\n")
    else:
        for r in results:
            alive = r.get("alive", False)
            status = "alive" if alive else "FAILED"
            count = r.get("tool_count", 0)
            elapsed = r.get("elapsed_ms", "?")
            stdout.write(f"[{status}] {r['name']}: {count} tools ({elapsed}ms)\n")
            if "error" in r:
                stdout.write(f"  error: {r['error']}\n")
    return 1 if any(not r.get("alive") for r in results) else 0


def _run_adopt(
    args: argparse.Namespace,
    root: pathlib.Path,
    *,
    stdout: typing.TextIO,
    stderr: typing.TextIO,
) -> int:
    """Run the adopt workflow: detect → plan → consent → apply."""
    from soleaux.provisioning import adopt as adopt_mod
    from soleaux.provisioning.contracts import AdoptExtraMissingError

    try:
        if args.revert:
            restored = adopt_mod.revert(root)
            if not restored:
                stderr.write("No backups found in .soleaux-backups/\n")
                return 1
            for rel in restored:
                stdout.write(f"reverted: {rel}\n")
            return 0

        report = adopt_mod.detect(root)
        plan = adopt_mod.build_plan(
            report,
            targets=tuple(args.target or ("editor", "mcp", "providers")),
            languages=tuple(args.language) if args.language else None,
        )

        stdout.write(adopt_mod.render_plan(plan))
        stdout.write("\n")
        if report.warnings:
            stderr.write("\nWarnings:\n")
            for warning in report.warnings:
                stderr.write(f"  - {warning}\n")

        if not plan.actions:
            return 0
        if args.dry_run:
            return 0

        if not args.yes:
            if not sys.stdin.isatty():
                stderr.write(
                    "Refusing to apply without a TTY. Pass --yes for non-interactive apply.\n"
                )
                return 1
            answer = input("\nApply this plan? [y/N] ").strip().lower()
            if answer not in {"y", "yes"}:
                stderr.write("aborted\n")
                return 1

        result = adopt_mod.apply_plan(plan, force=bool(args.force))
        for entry in result.written:
            stdout.write(f"wrote: {entry}\n")
        for entry in result.skipped:
            stdout.write(f"skipped (already in desired state): {entry}\n")
        for backup in result.backups:
            stdout.write(f"backed up: {backup.original_path} -> {backup.backup_path}\n")
        return 0
    except AdoptExtraMissingError as exc:
        stderr.write(f"{exc}\n")
        return 2


def main(argv: collections.abc.Sequence[str] | None = None) -> None:
    """Run a CLI command, defaulting to the real FastMCP stdio transport."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    parsed = create_parser().parse_args(arguments)
    if parsed.version:
        sys.stdout.write(f"soleaux {soleaux.analysis.service.product_version()}\n")
        return
    if parsed.command is None:
        _maybe_emit_first_run_nudge(parsed.root or pathlib.Path.cwd())
        from soleaux.server import create_server

        create_server(parsed.root).run()
        return
    raise SystemExit(asyncio.run(run_cli(arguments)))


def _maybe_emit_first_run_nudge(root: pathlib.Path) -> None:
    """Point new users at `soleaux adopt` when no soleaux.toml exists yet."""
    import importlib.util

    from soleaux.contracts.config import CONFIG_FILENAME

    if (root / CONFIG_FILENAME).is_file():
        return
    if not sys.stderr.isatty():
        return
    if any(importlib.util.find_spec(name) is None for name in ("json5", "psutil", "tomlkit")):
        return
    sys.stderr.write(
        "\n"
        "soleaux: no soleaux.toml found. Run `soleaux adopt` to detect existing\n"
        "language servers and generate a workspace configuration.\n\n"
    )
