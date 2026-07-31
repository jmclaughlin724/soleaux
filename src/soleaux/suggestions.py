"""Repo-content MCP server suggestions.

Read-only signal scan that matches known MCP servers against workspace
indicators (config files, package dependencies). No index, no state.
"""

from __future__ import annotations

import dataclasses
import json
import os
import pathlib

import soleaux.structural.path_patterns


@dataclasses.dataclass(frozen=True)
class McpSuggestion:
    """One known MCP server with detection signals."""

    name: str
    rationale: str
    command: list[str] | None = None
    url: str | None = None
    detect_files: tuple[str, ...] = ()
    detect_deps: tuple[str, ...] = ()
    auth_token_env_hint: str | None = None

    def matches(
        self,
        root: pathlib.Path,
        known_deps: set[str],
    ) -> bool:
        """Return True if any detection signal is present in the workspace."""
        for pattern in self.detect_files:
            if _glob_exists(root, pattern):
                return True
        return any(dep in known_deps for dep in self.detect_deps)

    def to_dict(self) -> dict[str, object]:
        d = dataclasses.asdict(self)
        return {k: v for k, v in d.items() if v is not None and v != () and v != ""}


CATALOG: tuple[McpSuggestion, ...] = (
    McpSuggestion(
        name="playwright",
        command=["pnpm", "dlx", "@playwright/mcp@latest", "--isolated"],
        detect_files=("playwright.config.ts", "playwright.config.js"),
        detect_deps=("@playwright/test",),
        rationale="Playwright config detected — browser automation MCP for E2E tests",
    ),
    McpSuggestion(
        name="eslint",
        command=["npx", "--yes", "@eslint/mcp@latest"],
        detect_files=("eslint.config.mjs", "eslint.config.js", ".eslintrc.json"),
        detect_deps=("eslint",),
        rationale="ESLint config detected — lint diagnostics MCP",
    ),
    McpSuggestion(
        name="shadcn",
        command=["pnpm", "dlx", "shadcn@latest", "mcp"],
        detect_files=("components.json",),
        rationale="shadcn/ui config detected — component registry MCP",
    ),
    McpSuggestion(
        name="next-devtools",
        command=["pnpm", "dlx", "next-devtools-mcp@latest"],
        detect_files=("next.config.ts", "next.config.mjs", "next.config.js"),
        detect_deps=("next",),
        rationale=(
            "Next.js project detected — devtools MCP for live dev-server state "
            "(errors, logs, compilation); routes come from framework.registrations"
        ),
    ),
    McpSuggestion(
        name="supabase",
        url="https://mcp.supabase.com/mcp",
        auth_token_env_hint="SUPABASE_ACCESS_TOKEN",
        detect_files=("supabase/config.toml",),
        rationale="Supabase project detected — database, auth, and RLS MCP",
    ),
    McpSuggestion(
        name="sentry",
        url="https://mcp.sentry.dev/mcp",
        auth_token_env_hint="SENTRY_AUTH_TOKEN",
        detect_files=("sentry.client.config.ts", "sentry.server.config.ts"),
        detect_deps=("@sentry/nextjs", "@sentry/node"),
        rationale="Sentry SDK detected — error tracking and release MCP",
    ),
    McpSuggestion(
        name="posthog",
        url="https://mcp.posthog.com/mcp",
        auth_token_env_hint="POSTHOG_PERSONAL_API_KEY",
        detect_files=("posthog.config.ts", "posthog.config.js"),
        detect_deps=("posthog-node", "posthog-js"),
        rationale="PostHog SDK detected — analytics and feature flag MCP",
    ),
    McpSuggestion(
        name="mintlify",
        url="https://mcp.mintlify.com/mcp",
        detect_files=("docs.json", "mint.json"),
        rationale="Mintlify docs detected — documentation MCP",
    ),
    McpSuggestion(
        name="airtable",
        url="https://mcp.airtable.com/mcp",
        auth_token_env_hint="AIRTABLE_ACCESS_TOKEN",
        detect_deps=("airtable",),
        rationale="Airtable SDK detected — base and record MCP",
    ),
    McpSuggestion(
        name="context7",
        command=["npx", "--yes", "@upstash/context7-mcp@latest"],
        rationale="General-purpose library docs MCP — add manually when needed",
    ),
    McpSuggestion(
        name="chrome-devtools",
        command=["npx", "--yes", "chrome-devtools-mcp@latest"],
        detect_deps=("puppeteer", "playwright-core"),
        rationale="Browser automation dependency detected — Chrome DevTools MCP",
    ),
    McpSuggestion(
        name="vercel",
        url="https://mcp.vercel.com/mcp",
        auth_token_env_hint="VERCEL_ACCESS_TOKEN",
        detect_files=("vercel.json",),
        rationale="Vercel config detected — deployment and project MCP",
    ),
    McpSuggestion(
        name="render",
        url="https://mcp.render.com/mcp",
        auth_token_env_hint="RENDER_API_KEY",
        detect_files=("render.yaml",),
        rationale="Render Blueprint detected — infrastructure MCP",
    ),
    McpSuggestion(
        name="zod",
        command=["npx", "--yes", "@hookdotdev/zod-mcp@latest"],
        detect_deps=("zod",),
        rationale="Zod detected — schema validation and type inference MCP",
    ),
    McpSuggestion(
        name="github",
        url="https://api.githubcopilot.com/mcp/",
        auth_token_env_hint="GITHUB_TOKEN",
        detect_files=(".github/workflows/ci.yml",),
        rationale="GitHub Actions detected — repository and CI MCP",
    ),
)


def scan_for_suggestions(root: pathlib.Path) -> list[McpSuggestion]:
    """Match catalog detection rules against workspace signals.

    Reads package.json and pyproject.toml dependencies if present,
    and checks for config file patterns. Returns matched suggestions
    in catalog order. No index, no state, no side effects.
    """
    deps = _read_package_deps(root) | _read_pyproject_deps(root)
    return [entry for entry in CATALOG if entry.matches(root, deps)]


def _read_package_deps(root: pathlib.Path) -> set[str]:
    """Extract dependency names from package.json if present."""
    pkg = root / "package.json"
    if not pkg.is_file():
        return set()
    try:
        raw = json.loads(pkg.read_text(encoding="utf-8"))
    except json.JSONDecodeError, UnicodeDecodeError:
        return set()
    data = raw
    deps: set[str] = set()
    for section in ("dependencies", "devDependencies", "peerDependencies"):
        section_data = data.get(section, {})
        if section_data:
            deps.update(str(k) for k in section_data)
    return deps


def _read_pyproject_deps(root: pathlib.Path) -> set[str]:
    """Extract dependency names from pyproject.toml if present."""
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return set()
    try:
        import tomllib

        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except Exception:
        return set()
    deps: set[str] = set()

    def _extract_name(raw: str) -> str:
        return raw.split(">")[0].split("<")[0].split("=")[0].split("!")[0].split("[")[0].strip()

    project = data.get("project", {})
    if project:
        raw_deps = project.get("dependencies", [])
        for dep in raw_deps:
            name = _extract_name(dep)
            if name:
                deps.add(name)
    dep_groups = data.get("dependency-groups", {})
    for group in dep_groups.values():
        for dep in group:
            name = _extract_name(dep)
            if name:
                deps.add(name)
    return deps


_MAX_PROBE_DEPTH = 2
_SKIPPED_PROBE_DIRECTORIES = frozenset(
    {".git", ".turbo", ".venv", "__pycache__", "dist", "node_modules"}
)


def _glob_exists(root: pathlib.Path, pattern: str) -> bool:
    """Check if any file matches the pattern under root (max depth 2)."""
    if (root / pattern).exists():
        return True
    compiled = soleaux.structural.path_patterns.RepositoryPattern.parse(pattern)
    for directory, subdirectories, filenames in os.walk(root):
        relative = pathlib.Path(directory).relative_to(root).as_posix()
        depth = 0 if relative == "." else relative.count("/") + 1
        subdirectories[:] = (
            []
            if depth >= _MAX_PROBE_DEPTH
            else [name for name in subdirectories if name not in _SKIPPED_PROBE_DIRECTORIES]
        )
        prefix = "" if depth == 0 else f"{relative}/"
        if any(compiled.matches(f"{prefix}{name}") for name in filenames):
            return True
    return False
