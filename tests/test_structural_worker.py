"""AC22 / AC23: supervised worker lifecycle, recycling, cancellation, and shutdown."""

import asyncio
import os
import pathlib
import subprocess
import sys
import unittest.mock

import _assertions
import pytest

import soleaux.contracts.budget
import soleaux.postgresql.runtime
import soleaux.structural.ast_runtime
import soleaux.structural.fragments
import soleaux.structural.supervisor

FIXTURE = b"def f():\n    return 1\n"


def _children() -> list[str]:
    result = subprocess.run(
        ["pgrep", "-P", str(os.getpid())],
        capture_output=True,
        text=True,
        check=False,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def _protocol_worker(
    extract_statement: str,
    *,
    identity: dict[str, object] | None = None,
) -> list[str]:
    handshake_identity = (
        {
            "engine": "python",
            "engine_version": soleaux.structural.fragments.AST_GREP_VERSION,
            "capabilities": list(soleaux.structural.fragments.STRUCTURAL_WORKER_CAPABILITIES),
        }
        if identity is None
        else identity
    )
    script = (
        "import json\n"
        "import sys\n"
        "import time\n"
        f"identity = {handshake_identity!r}\n"
        "for line in sys.stdin:\n"
        "    request = json.loads(line)\n"
        "    if request.get('op') == 'ping':\n"
        "        print(json.dumps({"
        "'id': request.get('id'), 'status': 'ok', 'op': 'pong', "
        "**identity"
        "}), flush=True)\n"
        "    elif request.get('op') == 'extract':\n"
        f"        {extract_statement}\n"
        "    elif request.get('op') == 'shutdown':\n"
        "        raise SystemExit(0)\n"
    )
    return [sys.executable, "-c", script]


def _process_tree_worker(
    marker: pathlib.Path,
    *,
    hang: bool = True,
    child_ignores_terminate: bool = False,
) -> list[str]:
    identity = {
        "engine": "python",
        "engine_version": soleaux.structural.fragments.AST_GREP_VERSION,
        "capabilities": list(soleaux.structural.fragments.STRUCTURAL_WORKER_CAPABILITIES),
    }
    script = (
        "import json\n"
        "import os\n"
        "import subprocess\n"
        "import sys\n"
        "import time\n"
        f"identity = {identity!r}\n"
        f"hang = {hang!r}\n"
        f"child_ignores_terminate = {child_ignores_terminate!r}\n"
        "marker = sys.argv[1]\n"
        "for line in sys.stdin:\n"
        "    request = json.loads(line)\n"
        "    if request.get('op') == 'ping':\n"
        "        print(json.dumps({"
        "'id': request.get('id'), 'status': 'ok', 'op': 'pong', "
        "**identity"
        "}), flush=True)\n"
        "    elif request.get('op') in {'extract', 'structural'}:\n"
        "        child = subprocess.Popen(\n"
        "            [\n"
        "                sys.executable,\n"
        "                '-c',\n"
        "                (\n"
        "                    'import signal; import time; '\n"
        "                    'signal.signal(signal.SIGTERM, signal.SIG_IGN); '\n"
        "                    'time.sleep(60)'\n"
        "                    if child_ignores_terminate\n"
        "                    else 'import time; time.sleep(60)'\n"
        "                ),\n"
        "            ],\n"
        "            stdin=subprocess.DEVNULL,\n"
        "            stdout=subprocess.DEVNULL,\n"
        "            stderr=subprocess.DEVNULL,\n"
        "        )\n"
        "        if child_ignores_terminate:\n"
        "            time.sleep(0.05)\n"
        "        with open(marker, 'a', encoding='utf-8') as output:\n"
        "            output.write(f'{os.getpid()} {child.pid}\\n')\n"
        "            output.flush()\n"
        "        if hang:\n"
        "            time.sleep(60)\n"
        "        else:\n"
        "            print(json.dumps({\n"
        "                'id': request.get('id'),\n"
        "                'status': 'ok',\n"
        "                'fragments': [],\n"
        "                'diagnostics': [],\n"
        "                'stats': {\n"
        "                    'parses': 1,\n"
        "                    'parse_ms': 0.0,\n"
        "                    'truncated': False,\n"
        "                    'unsupported': [],\n"
        "                },\n"
        "            }), flush=True)\n"
        "    elif request.get('op') == 'shutdown':\n"
        "        raise SystemExit(0)\n"
    )
    return [sys.executable, "-c", script, str(marker)]


async def _wait_for_process_pairs(
    marker: pathlib.Path,
    *,
    count: int,
) -> tuple[tuple[int, int], ...]:
    for _ in range(200):
        if marker.exists():
            rows = tuple(
                tuple(int(value) for value in line.split())
                for line in marker.read_text(encoding="utf-8").splitlines()
                if line
            )
            if len(rows) >= count:
                return tuple((row[0], row[1]) for row in rows[:count])
        await asyncio.sleep(0.01)
    raise AssertionError(f"structural worker did not record {count} process trees")


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _assert_isolated_process_group(worker_pid: int, child_pid: int) -> None:
    if os.name != "posix":
        return
    assert os.getpgid(worker_pid) == worker_pid
    assert os.getpgid(child_pid) == worker_pid
    assert worker_pid != os.getpgrp()


async def _assert_processes_exit(*pids: int) -> None:
    for _ in range(200):
        if not any(_process_exists(pid) for pid in pids):
            return
        await asyncio.sleep(0.01)
    survivors = [pid for pid in pids if _process_exists(pid)]
    raise AssertionError(f"structural worker process tree survived shutdown: {survivors}")


async def test_lazy_start_and_single_worker() -> None:
    supervisor = soleaux.structural.supervisor.StructuralWorkerSupervisor()
    try:
        assert supervisor.started is False
        first = await supervisor.extract(
            language="Python",
            path="a.py",
            content=FIXTURE,
            projections=("syntax.declarations",),
        )
        assert supervisor.started is True
        pid = supervisor.pid
        assert pid is not None
        await supervisor.extract(
            language="Python",
            path="a.py",
            content=FIXTURE,
            projections=("syntax.declarations",),
        )
        assert supervisor.pid == pid
        assert first.parses == 1
    finally:
        await supervisor.aclose()
    assert supervisor.started is False
    assert _children() == []


async def test_default_worker_is_isolated_from_workspace_modules_and_host_secrets(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shadow_package = tmp_path / "soleaux" / "structural"
    shadow_package.mkdir(parents=True)
    (shadow_package.parent / "__init__.py").write_text("", encoding="utf-8")
    (shadow_package / "__init__.py").write_text("", encoding="utf-8")
    (shadow_package / "worker.py").write_text(
        "raise RuntimeError('workspace module shadowed structural worker')\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SOLEAUX_TEST_UNLISTED_SECRET", "must-not-reach-worker")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    spawn = unittest.mock.AsyncMock(wraps=asyncio.create_subprocess_exec)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    supervisor = soleaux.structural.supervisor.StructuralWorkerSupervisor()
    try:
        result = await supervisor.extract(
            language="Python",
            path="a.py",
            content=FIXTURE,
            projections=("syntax.declarations",),
        )
        assert result.parses == 1
    finally:
        await supervisor.aclose()

    assert spawn.call_args_list
    argv = spawn.call_args_list[0].args
    assert argv[:4] == (
        sys.executable,
        "-I",
        "-m",
        "soleaux.structural.worker",
    )
    environment = spawn.call_args_list[0].kwargs["env"]
    assert "SOLEAUX_TEST_UNLISTED_SECRET" not in environment
    assert "PYTHONPATH" not in environment
    assert environment == soleaux.postgresql.runtime.build_safe_environment(
        {},
        environment_names=(),
    )
    assert spawn.call_args_list[0].kwargs["start_new_session"] is True
    assert "creationflags" not in spawn.call_args_list[0].kwargs


def test_one_shot_ast_worker_is_isolated_from_workspace_modules_and_host_secrets(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shadow_package = tmp_path / "soleaux" / "structural"
    shadow_package.mkdir(parents=True)
    (shadow_package.parent / "__init__.py").write_text("", encoding="utf-8")
    (shadow_package / "__init__.py").write_text("", encoding="utf-8")
    (shadow_package / "ast_worker.py").write_text(
        "raise RuntimeError('workspace module shadowed ast worker')\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SOLEAUX_TEST_UNLISTED_SECRET", "must-not-reach-worker")
    run = unittest.mock.Mock(wraps=subprocess.run)
    monkeypatch.setattr(soleaux.structural.ast_runtime.subprocess, "run", run)

    assert (
        soleaux.structural.ast_runtime.replace_json_value(
            '{"enabled": true}\n',
            "enabled",
            "false",
        )
        == '{"enabled": false}\n'
    )
    argv = run.call_args.args[0]
    assert argv == [
        sys.executable,
        "-I",
        "-m",
        "soleaux.structural.ast_worker",
    ]
    environment = run.call_args.kwargs["env"]
    assert "SOLEAUX_TEST_UNLISTED_SECRET" not in environment
    assert environment == soleaux.postgresql.runtime.build_safe_environment(
        {},
        environment_names=(),
    )


async def test_recycle_after_completed_job_limit() -> None:
    budget = soleaux.contracts.budget.StructuralWorkerBudget(
        max_completed_jobs=2, shutdown_grace_seconds=2.0
    )
    supervisor = soleaux.structural.supervisor.StructuralWorkerSupervisor(budget)
    try:
        first_pid = None
        for index in range(2):
            await supervisor.extract(
                language="Python",
                path=f"a{index}.py",
                content=FIXTURE,
                projections=("syntax.declarations",),
            )
            first_pid = supervisor.pid
        await supervisor.extract(
            language="Python",
            path="a2.py",
            content=FIXTURE,
            projections=("syntax.declarations",),
        )
        assert supervisor.pid != first_pid
        assert supervisor.completed_jobs == 1
    finally:
        await supervisor.aclose()
    assert _children() == []


async def test_recycle_after_rss_limit() -> None:
    budget = soleaux.contracts.budget.StructuralWorkerBudget(
        max_rss_bytes=1, shutdown_grace_seconds=2.0
    )
    supervisor = soleaux.structural.supervisor.StructuralWorkerSupervisor(budget)
    try:
        first_pid = None
        await supervisor.extract(
            language="Python",
            path="a.py",
            content=FIXTURE,
            projections=("syntax.declarations",),
        )
        first_pid = supervisor.pid
        await supervisor.extract(
            language="Python",
            path="b.py",
            content=FIXTURE,
            projections=("syntax.declarations",),
        )
        assert supervisor.pid != first_pid
    finally:
        await supervisor.aclose()
    assert _children() == []


async def test_cancellation_replaces_the_worker() -> None:
    supervisor = soleaux.structural.supervisor.StructuralWorkerSupervisor()
    try:
        with pytest.raises((TimeoutError, asyncio.TimeoutError)):
            await asyncio.wait_for(
                supervisor.extract(
                    language="Python",
                    path="a.py",
                    content=FIXTURE,
                    projections=("syntax.declarations",),
                ),
                timeout=0.001,
            )
        result = await supervisor.extract(
            language="Python",
            path="a.py",
            content=FIXTURE,
            projections=("syntax.declarations",),
        )
        assert result.parses == 1
    finally:
        await supervisor.aclose()
    assert _children() == []


async def test_cancellation_reaps_worker_and_descendant(tmp_path: pathlib.Path) -> None:
    marker = tmp_path / "cancellation-process-tree.txt"
    budget = soleaux.contracts.budget.StructuralWorkerBudget(shutdown_grace_seconds=0.2)
    supervisor = soleaux.structural.supervisor.StructuralWorkerSupervisor(
        budget,
        worker_argv=_process_tree_worker(marker),
    )
    task = asyncio.create_task(
        supervisor.extract(
            language="Python",
            path="a.py",
            content=FIXTURE,
            projections=("syntax.declarations",),
            timeout=10.0,
        )
    )
    pids: tuple[int, ...] = ()
    try:
        pairs = await _wait_for_process_pairs(marker, count=1)
        pids = tuple(pid for pair in pairs for pid in pair)
        assert all(_process_exists(pid) for pid in pids)
        _assert_isolated_process_group(*pairs[0])

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await supervisor.aclose()

    assert pids
    await _assert_processes_exit(*pids)


async def test_aclose_cancels_in_flight_extract_without_retrying_worker(
    tmp_path: pathlib.Path,
) -> None:
    marker = tmp_path / "extract-aclose-process-tree.txt"
    budget = soleaux.contracts.budget.StructuralWorkerBudget(shutdown_grace_seconds=0.2)
    supervisor = soleaux.structural.supervisor.StructuralWorkerSupervisor(
        budget,
        worker_argv=_process_tree_worker(
            marker,
            child_ignores_terminate=True,
        ),
    )
    task = asyncio.create_task(
        supervisor.extract(
            language="Python",
            path="a.py",
            content=FIXTURE,
            projections=("syntax.declarations",),
            timeout=1.0,
        )
    )
    pids: tuple[int, ...] = ()
    try:
        pair = (await _wait_for_process_pairs(marker, count=1))[0]
        pids = pair
        _assert_isolated_process_group(*pair)

        await supervisor.aclose()

        with pytest.raises(asyncio.CancelledError):
            await task
        assert marker.read_text(encoding="utf-8").splitlines() == [f"{pair[0]} {pair[1]}"]
        with _assertions.raises_with_message(
            soleaux.structural.supervisor.WorkerUnavailableError,
            "structural worker supervisor is closed",
        ):
            await supervisor.extract(
                language="Python",
                path="after-close.py",
                content=FIXTURE,
                projections=("syntax.declarations",),
            )
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await supervisor.aclose()

    await _assert_processes_exit(*pids)


async def test_aclose_cancels_in_flight_structural_without_retrying_worker(
    tmp_path: pathlib.Path,
) -> None:
    marker = tmp_path / "structural-aclose-process-tree.txt"
    budget = soleaux.contracts.budget.StructuralWorkerBudget(shutdown_grace_seconds=0.2)
    supervisor = soleaux.structural.supervisor.StructuralWorkerSupervisor(
        budget,
        worker_argv=_process_tree_worker(
            marker,
            child_ignores_terminate=True,
        ),
    )
    task = asyncio.create_task(
        supervisor.structural(
            language="Python",
            matcher={"kind": "identifier"},
            files=(("a.py", FIXTURE),),
            timeout=1.0,
        )
    )
    pids: tuple[int, ...] = ()
    try:
        pair = (await _wait_for_process_pairs(marker, count=1))[0]
        pids = pair
        _assert_isolated_process_group(*pair)

        await supervisor.aclose()

        with pytest.raises(asyncio.CancelledError):
            await task
        assert marker.read_text(encoding="utf-8").splitlines() == [f"{pair[0]} {pair[1]}"]
        with _assertions.raises_with_message(
            soleaux.structural.supervisor.WorkerUnavailableError,
            "structural worker supervisor is closed",
        ):
            await supervisor.structural(
                language="Python",
                matcher={"kind": "identifier"},
                files=(("after-close.py", FIXTURE),),
            )
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await supervisor.aclose()

    await _assert_processes_exit(*pids)


async def test_worker_timeout_retries_then_surfaces_unavailable() -> None:
    supervisor = soleaux.structural.supervisor.StructuralWorkerSupervisor(
        worker_argv=_protocol_worker("time.sleep(60)"),
    )
    try:
        with _assertions.raises_with_message(
            soleaux.structural.supervisor.WorkerUnavailableError, "missed its deadline"
        ):
            await supervisor.extract(
                language="Python",
                path="a.py",
                content=FIXTURE,
                projections=("syntax.declarations",),
                timeout=0.01,
            )
    finally:
        await supervisor.aclose()
    assert _children() == []


async def test_worker_timeout_reaps_each_worker_and_descendant(
    tmp_path: pathlib.Path,
) -> None:
    marker = tmp_path / "timeout-process-trees.txt"
    budget = soleaux.contracts.budget.StructuralWorkerBudget(shutdown_grace_seconds=0.2)
    supervisor = soleaux.structural.supervisor.StructuralWorkerSupervisor(
        budget,
        worker_argv=_process_tree_worker(marker),
    )
    pids: tuple[int, ...] = ()
    try:
        with _assertions.raises_with_message(
            soleaux.structural.supervisor.WorkerUnavailableError,
            "missed its deadline",
        ):
            await supervisor.extract(
                language="Python",
                path="a.py",
                content=FIXTURE,
                projections=("syntax.declarations",),
                timeout=0.05,
            )
        pairs = await _wait_for_process_pairs(marker, count=2)
        pids = tuple(pid for pair in pairs for pid in pair)
    finally:
        await supervisor.aclose()

    assert len(pids) == 4
    await _assert_processes_exit(*pids)


async def test_worker_replacement_reaps_replaced_descendant(
    tmp_path: pathlib.Path,
) -> None:
    marker = tmp_path / "replacement-process-trees.txt"
    budget = soleaux.contracts.budget.StructuralWorkerBudget(
        max_completed_jobs=1,
        shutdown_grace_seconds=0.2,
    )
    supervisor = soleaux.structural.supervisor.StructuralWorkerSupervisor(
        budget,
        worker_argv=_process_tree_worker(
            marker,
            hang=False,
            child_ignores_terminate=True,
        ),
    )
    pids: tuple[int, ...] = ()
    try:
        await supervisor.extract(
            language="Python",
            path="a.py",
            content=FIXTURE,
            projections=("syntax.declarations",),
        )
        first_pair = (await _wait_for_process_pairs(marker, count=1))[0]
        _assert_isolated_process_group(*first_pair)

        await supervisor.extract(
            language="Python",
            path="b.py",
            content=FIXTURE,
            projections=("syntax.declarations",),
        )
        pairs = await _wait_for_process_pairs(marker, count=2)
        pids = tuple(pid for pair in pairs for pid in pair)
        await _assert_processes_exit(*first_pair)
        assert all(_process_exists(pid) for pid in pairs[1])
        _assert_isolated_process_group(*pairs[1])
    finally:
        await supervisor.aclose()

    assert len(pids) == 4
    await _assert_processes_exit(*pids)


async def test_worker_shutdown_reaps_descendant(tmp_path: pathlib.Path) -> None:
    marker = tmp_path / "shutdown-process-tree.txt"
    budget = soleaux.contracts.budget.StructuralWorkerBudget(shutdown_grace_seconds=0.2)
    supervisor = soleaux.structural.supervisor.StructuralWorkerSupervisor(
        budget,
        worker_argv=_process_tree_worker(
            marker,
            hang=False,
            child_ignores_terminate=True,
        ),
    )
    await supervisor.extract(
        language="Python",
        path="a.py",
        content=FIXTURE,
        projections=("syntax.declarations",),
    )
    pair = (await _wait_for_process_pairs(marker, count=1))[0]
    assert all(_process_exists(pid) for pid in pair)
    _assert_isolated_process_group(*pair)

    await supervisor.aclose()

    await _assert_processes_exit(*pair)


async def test_malformed_worker_output_retries_then_surfaces_unavailable() -> None:
    supervisor = soleaux.structural.supervisor.StructuralWorkerSupervisor(
        worker_argv=_protocol_worker("print('not-json', flush=True)"),
    )
    try:
        with _assertions.raises_with_message(
            soleaux.structural.supervisor.WorkerUnavailableError, "malformed frame"
        ):
            await supervisor.extract(
                language="Python",
                path="a.py",
                content=FIXTURE,
                projections=("syntax.declarations",),
            )
    finally:
        await supervisor.aclose()
    assert _children() == []


@pytest.mark.parametrize(
    "identity",
    [
        pytest.param(
            {
                "engine_version": soleaux.structural.fragments.AST_GREP_VERSION,
                "capabilities": list(soleaux.structural.fragments.STRUCTURAL_WORKER_CAPABILITIES),
            },
            id="missing-engine",
        ),
        pytest.param(
            {
                "engine": "rust",
                "engine_version": soleaux.structural.fragments.AST_GREP_VERSION,
                "capabilities": list(soleaux.structural.fragments.STRUCTURAL_WORKER_CAPABILITIES),
            },
            id="wrong-engine",
        ),
        pytest.param(
            {
                "engine": "python",
                "capabilities": list(soleaux.structural.fragments.STRUCTURAL_WORKER_CAPABILITIES),
            },
            id="missing-version",
        ),
        pytest.param(
            {
                "engine": "python",
                "engine_version": "unexpected",
                "capabilities": list(soleaux.structural.fragments.STRUCTURAL_WORKER_CAPABILITIES),
            },
            id="wrong-version",
        ),
        pytest.param(
            {
                "engine": "python",
                "engine_version": soleaux.structural.fragments.AST_GREP_VERSION,
            },
            id="missing-capabilities",
        ),
        pytest.param(
            {
                "engine": "python",
                "engine_version": soleaux.structural.fragments.AST_GREP_VERSION,
                "capabilities": ["unexpected"],
            },
            id="wrong-capabilities",
        ),
    ],
)
async def test_worker_identity_mismatch_fails_closed(identity: dict[str, object]) -> None:
    supervisor = soleaux.structural.supervisor.StructuralWorkerSupervisor(
        worker_argv=_protocol_worker(
            "raise SystemExit(3)",
            identity=identity,
        ),
    )
    try:
        with _assertions.raises_with_message(
            soleaux.structural.supervisor.WorkerUnavailableError,
            "did not prove the expected python engine/version/capability identity",
        ):
            await supervisor.extract(
                language="Python",
                path="a.py",
                content=FIXTURE,
                projections=("syntax.declarations",),
            )
    finally:
        await supervisor.aclose()
    assert _children() == []


async def test_missing_worker_executable_is_a_typed_failure() -> None:
    supervisor = soleaux.structural.supervisor.StructuralWorkerSupervisor(
        worker_argv=["soleaux-structural-worker-does-not-exist"],
    )
    try:
        with _assertions.raises_with_message(
            soleaux.structural.supervisor.WorkerUnavailableError,
            "structural worker could not start",
        ):
            await supervisor.extract(
                language="Python",
                path="a.py",
                content=FIXTURE,
                projections=("syntax.declarations",),
            )
    finally:
        await supervisor.aclose()


def test_unsupported_process_tree_platform_is_a_typed_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(soleaux.structural.supervisor.os, "name", "unsupported")

    with _assertions.raises_with_message(
        soleaux.structural.supervisor.WorkerUnavailableError,
        "process-tree isolation is unsupported",
    ):
        soleaux.structural.supervisor._require_process_tree_isolation()


async def test_shutdown_leaves_no_child() -> None:
    supervisor = soleaux.structural.supervisor.StructuralWorkerSupervisor()
    await supervisor.extract(
        language="Python",
        path="a.py",
        content=FIXTURE,
        projections=("syntax.declarations",),
    )
    pid = supervisor.pid
    assert pid is not None
    await supervisor.aclose()
    assert supervisor.started is False
    assert _children() == []
    result = subprocess.run(["kill", "-0", str(pid)], capture_output=True, check=False)
    assert result.returncode != 0
