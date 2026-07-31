from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_HOOK_PATH = _REPOSITORY_ROOT / ".codex" / "hooks" / "UserPromptSubmit" / "soleaux_context.py"


def _load_hook() -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        "codex_user_prompt_submit_soleaux_context",
        _HOOK_PATH,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("could not load the UserPromptSubmit hook")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


hook = _load_hook()


def _payload(cwd: Path = _REPOSITORY_ROOT, prompt: str = "Find the owner") -> str:
    return json.dumps(
        {
            "cwd": str(cwd),
            "hook_event_name": "UserPromptSubmit",
            "prompt": prompt,
            "turn_id": "turn-1",
        }
    )


def test_parses_native_prompt_input() -> None:
    parsed = hook.parse_prompt_input(_payload())

    assert parsed.cwd == _REPOSITORY_ROOT
    assert parsed.prompt == "Find the owner"


@pytest.mark.parametrize(
    ("payload", "cause"),
    [
        ("{", "stdin must contain one JSON object"),
        ("[]", "stdin must contain one JSON object"),
        (
            json.dumps(
                {
                    "cwd": str(_REPOSITORY_ROOT),
                    "hook_event_name": "PreToolUse",
                    "prompt": "x",
                    "turn_id": "turn-1",
                }
            ),
            "expected hook_event_name UserPromptSubmit",
        ),
        (
            json.dumps(
                {
                    "cwd": ".",
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": "x",
                    "turn_id": "turn-1",
                }
            ),
            "cwd must be absolute",
        ),
    ],
)
def test_rejects_invalid_native_input(payload: str, cause: str) -> None:
    with pytest.raises(hook.HookFailure, match=cause):
        hook.parse_prompt_input(payload)


def test_loads_exact_scoped_codex_bridge() -> None:
    launcher = hook.load_soleaux_launcher(_REPOSITORY_ROOT)

    assert launcher.python_path == _REPOSITORY_ROOT / ".venv/bin/python"
    assert launcher.client_path == _REPOSITORY_ROOT / "scripts/soleaux/client.py"
    assert launcher.timeout_seconds == 60


def test_requests_context_once_without_credentials_in_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[str, ...], str]] = []
    launcher = hook.SoleauxLauncher(
        client_path=_REPOSITORY_ROOT / "scripts/soleaux/client.py",
        python_path=_REPOSITORY_ROOT / ".venv/bin/python",
        timeout_seconds=60,
    )

    def fake_run(arguments: tuple[str, ...], **options: object):
        calls.append((arguments, str(options["input"])))
        return subprocess.CompletedProcess(arguments, 0, "context packet\n", "")

    monkeypatch.setattr(hook.subprocess, "run", fake_run)

    result = hook.request_task_context("Find the owner", launcher)

    assert result == "context packet"
    assert calls == [
        (
            (
                str(_REPOSITORY_ROOT / ".venv/bin/python"),
                str(_REPOSITORY_ROOT / "scripts/soleaux/client.py"),
                "context",
                "codex",
            ),
            "Find the owner",
        )
    ]
    assert "Bearer" not in " ".join(calls[0][0])


def test_builds_one_native_additional_context_packet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = hook.load_soleaux_launcher(_REPOSITORY_ROOT)
    monkeypatch.setattr(hook, "find_repository_root", lambda _cwd: _REPOSITORY_ROOT)
    monkeypatch.setattr(hook, "load_soleaux_launcher", lambda _root: launcher)
    monkeypatch.setattr(
        hook,
        "request_task_context",
        lambda prompt, _launcher: f"context for {prompt}",
    )

    assert hook.build_hook_output(_payload()) == {
        "hookSpecificOutput": {
            "additionalContext": "context for Find the owner",
            "hookEventName": "UserPromptSubmit",
        }
    }


def test_context_failure_is_bounded_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = hook.load_soleaux_launcher(_REPOSITORY_ROOT)
    secret = "credential-must-not-appear"

    def fake_run(arguments: tuple[str, ...], **_options: object):
        return subprocess.CompletedProcess(arguments, 2, "", secret)

    monkeypatch.setattr(hook.subprocess, "run", fake_run)

    with pytest.raises(hook.HookFailure) as captured:
        hook.request_task_context("Find the owner", launcher)

    line = hook._failure_line(captured.value)
    assert "code=context_unavailable" in line
    assert secret not in line


def test_context_above_the_native_hook_limit_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = hook.load_soleaux_launcher(_REPOSITORY_ROOT)

    def fake_run(arguments: tuple[str, ...], **_options: object):
        return subprocess.CompletedProcess(
            arguments,
            0,
            f"{'x' * (hook._MAX_CONTEXT_BYTES + 1)}\n",
            "",
        )

    monkeypatch.setattr(hook.subprocess, "run", fake_run)

    with pytest.raises(hook.HookFailure) as captured:
        hook.request_task_context("Find the owner", launcher)

    assert captured.value.code == "context_invalid"


def test_malformed_entrypoint_input_exits_two_with_corrective_stderr() -> None:
    result = subprocess.run(
        (str(_REPOSITORY_ROOT / ".venv/bin/python"), str(_HOOK_PATH)),
        cwd=_REPOSITORY_ROOT,
        input="{",
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert "source=.codex/hooks/UserPromptSubmit/soleaux_context.py" in result.stderr
    assert "code=invalid_input" in result.stderr
    assert "Corrective action:" in result.stderr
