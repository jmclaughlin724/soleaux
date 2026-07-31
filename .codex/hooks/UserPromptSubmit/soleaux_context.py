"""Inject one Soleaux task-context packet before Codex processes a prompt."""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
import tomllib
from pathlib import Path

_EXPECTED_EVENT = "UserPromptSubmit"
_HOOK_SOURCE = ".codex/hooks/UserPromptSubmit/soleaux_context.py"
_MAX_CONTEXT_BYTES = 65_536
_SUCCESS = 0


@dataclasses.dataclass(frozen=True, slots=True)
class PromptInput:
    cwd: Path
    prompt: str


@dataclasses.dataclass(frozen=True, slots=True)
class SoleauxLauncher:
    client_path: Path
    python_path: Path
    timeout_seconds: float


class HookFailure(Exception):
    def __init__(self, code: str, cause: str, corrective_action: str) -> None:
        super().__init__(cause)
        self.code = code
        self.cause = cause
        self.corrective_action = corrective_action


def _require_nonempty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise HookFailure(
            "invalid_input",
            f"{label} must be a nonempty string",
            "retry the prompt from a fresh Codex task with a valid native hook payload.",
        )
    return value


def parse_prompt_input(source: str) -> PromptInput:
    try:
        value = json.loads(source)
    except (json.JSONDecodeError, TypeError) as error:
        raise HookFailure(
            "invalid_input",
            "stdin must contain one JSON object",
            "retry the prompt from a fresh Codex task with valid hook JSON.",
        ) from error
    if not isinstance(value, dict):
        raise HookFailure(
            "invalid_input",
            "stdin must contain one JSON object",
            "retry the prompt from a fresh Codex task with valid hook JSON.",
        )
    if value.get("hook_event_name") != _EXPECTED_EVENT:
        raise HookFailure(
            "invalid_input",
            f"expected hook_event_name {_EXPECTED_EVENT}",
            "register this owner only for the Codex UserPromptSubmit event.",
        )
    cwd = Path(_require_nonempty_string(value.get("cwd"), "cwd"))
    if not cwd.is_absolute():
        raise HookFailure(
            "invalid_input",
            "cwd must be absolute",
            "retry the prompt from an absolute working directory inside the repository.",
        )
    _require_nonempty_string(value.get("turn_id"), "turn_id")
    return PromptInput(
        cwd=cwd,
        prompt=_require_nonempty_string(value.get("prompt"), "prompt"),
    )


def find_repository_root(cwd: Path) -> Path:
    try:
        canonical_cwd = cwd.resolve(strict=True)
        # S607: `git` is resolved through PATH deliberately, because the hook
        # must use whichever git the developer's environment provides.
        result = subprocess.run(
            ("git", "rev-parse", "--show-toplevel"),
            cwd=canonical_cwd,
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise HookFailure(
            "repository_unavailable",
            "the repository root could not be resolved",
            "run the prompt from a readable Git worktree and retry.",
        ) from error
    if result.returncode != _SUCCESS or not result.stdout.strip():
        raise HookFailure(
            "repository_unavailable",
            "git rev-parse did not return a repository root",
            "run the prompt from inside the repository worktree and retry.",
        )
    try:
        repository_root = Path(result.stdout.strip()).resolve(strict=True)
        canonical_cwd.relative_to(repository_root)
    except (OSError, ValueError) as error:
        raise HookFailure(
            "repository_unavailable",
            "cwd is not contained by the resolved repository root",
            "run the prompt from inside the repository worktree and retry.",
        ) from error
    return repository_root


def _positive_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise HookFailure(
            "invalid_configuration",
            f"{label} must be a positive number",
            "repair the Soleaux entry in .codex/config.toml and restart Codex.",
        )
    return float(value)


def load_soleaux_launcher(repository_root: Path) -> SoleauxLauncher:
    try:
        config = tomllib.loads(
            (repository_root / ".codex" / "config.toml").read_text(encoding="utf-8")
        )
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise HookFailure(
            "invalid_configuration",
            "the Codex MCP configuration could not be loaded",
            "repair .codex/config.toml and restart Codex.",
        ) from error
    servers = config.get("mcp_servers")
    soleaux = servers.get("soleaux") if isinstance(servers, dict) else None
    expected_arguments = ["scripts/soleaux/client.py", "bridge", "codex"]
    if (
        not isinstance(soleaux, dict)
        or soleaux.get("enabled") is not True
        or soleaux.get("command") != ".venv/bin/python"
        or soleaux.get("args") != expected_arguments
        or soleaux.get("cwd") != "."
    ):
        raise HookFailure(
            "invalid_configuration",
            "the configured Soleaux MCP launcher does not use the scoped Codex bridge",
            "repair mcp_servers.soleaux in .codex/config.toml and restart Codex.",
        )
    python_path = repository_root / ".venv" / "bin" / "python"
    client_path = repository_root / "scripts" / "soleaux" / "client.py"
    if not python_path.is_file() or not client_path.is_file():
        raise HookFailure(
            "dependency_unavailable",
            "the prepared Soleaux client runtime is unavailable",
            "run the root uv sync and retry from a fresh Codex task.",
        )
    return SoleauxLauncher(
        client_path=client_path,
        python_path=python_path,
        timeout_seconds=_positive_number(
            soleaux.get("tool_timeout_sec", 60),
            "mcp_servers.soleaux.tool_timeout_sec",
        ),
    )


def request_task_context(prompt: str, launcher: SoleauxLauncher) -> str:
    try:
        # S603: argv is a tuple, so no shell is involved, and both paths are
        # resolved from the launcher configuration rather than from the prompt.
        result = subprocess.run(
            (
                str(launcher.python_path),
                str(launcher.client_path),
                "context",
                "codex",
            ),
            capture_output=True,
            check=False,
            input=prompt,
            text=True,
            timeout=launcher.timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise HookFailure(
            "context_unavailable",
            "the Soleaux task-context request could not start",
            "run `pnpm soleaux:service:status`, repair the service, and retry.",
        ) from error
    context = result.stdout.strip()
    if result.returncode != _SUCCESS or not context:
        raise HookFailure(
            "context_unavailable",
            "the Soleaux task-context request failed",
            "run `pnpm soleaux:service:status`, repair the service, and retry.",
        )
    if len(context.encode("utf-8")) > _MAX_CONTEXT_BYTES:
        raise HookFailure(
            "context_invalid",
            "the Soleaux task-context response exceeded the Codex hook byte limit",
            "repair the Soleaux context renderer and retry from a fresh Codex task.",
        )
    return context


def build_hook_output(source: str) -> dict[str, object]:
    prompt_input = parse_prompt_input(source)
    repository_root = find_repository_root(prompt_input.cwd)
    launcher = load_soleaux_launcher(repository_root)
    additional_context = request_task_context(prompt_input.prompt, launcher)
    return {
        "hookSpecificOutput": {
            "additionalContext": additional_context,
            "hookEventName": _EXPECTED_EVENT,
        }
    }


def _failure_line(error: HookFailure) -> str:
    return (
        f"source={_HOOK_SOURCE} code={error.code} cause={error.cause} "
        f"Corrective action: {error.corrective_action}"
    )


def main() -> int:
    try:
        output = build_hook_output(sys.stdin.read())
    except HookFailure as error:
        sys.stderr.write(f"{_failure_line(error)}\n")
        return 2
    except Exception:
        sys.stderr.write(
            f"source={_HOOK_SOURCE} code=unexpected_failure "
            "cause=the pre-prompt context owner failed "
            "Corrective action: inspect the focused hook tests and retry from a fresh task.\n"
        )
        return 2
    sys.stdout.write(f"{json.dumps(output, separators=(',', ':'))}\n")
    return _SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())
