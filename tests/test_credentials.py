"""Per-backend OAuth token store construction and clearing (D035)."""

from __future__ import annotations

import pathlib
import stat

import pytest
from key_value.aio.stores.disk import DiskStore
from key_value.aio.stores.keyring.store import KeyringStore

import soleaux.credentials
from soleaux.contracts.config import McpBackendConfig


def _oauth_backend(**overrides: object) -> McpBackendConfig:
    payload: dict[str, object] = {"url": "https://example.com/mcp", "auth": "oauth"}
    payload.update(overrides)
    return McpBackendConfig.model_validate(payload)


def _isolate_token_root(monkeypatch: pytest.MonkeyPatch, root: pathlib.Path) -> None:
    monkeypatch.setattr(soleaux.credentials, "token_store_root", lambda: root)


def test_token_store_directory_scopes_each_backend_under_the_root(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolate_token_root(monkeypatch, tmp_path)

    assert soleaux.credentials.token_store_directory("alpha") == tmp_path / "alpha"
    assert soleaux.credentials.token_store_directory("beta") == tmp_path / "beta"


def test_disk_store_creates_a_private_directory_per_backend(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolate_token_root(monkeypatch, tmp_path)

    alpha = soleaux.credentials.build_token_store(_oauth_backend(), backend_name="alpha")
    beta = soleaux.credentials.build_token_store(_oauth_backend(), backend_name="beta")

    assert isinstance(alpha, DiskStore)
    assert isinstance(beta, DiskStore)
    assert alpha is not beta
    for name in ("alpha", "beta"):
        directory = tmp_path / name
        assert directory.is_dir()
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700


def test_disk_store_tightens_a_preexisting_broad_directory(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolate_token_root(monkeypatch, tmp_path)
    directory = tmp_path / "alpha"
    directory.mkdir()
    directory.chmod(0o755)

    soleaux.credentials.build_token_store(_oauth_backend(), backend_name="alpha")

    assert stat.S_IMODE(directory.stat().st_mode) == 0o700


def test_keyring_store_selection_does_not_touch_the_disk_root(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolate_token_root(monkeypatch, tmp_path)

    # py-key-value-aio marks the keyring store as unstable upstream.
    with pytest.warns(UserWarning, match="unstable"):
        store = soleaux.credentials.build_token_store(
            _oauth_backend(token_store="keyring"), backend_name="alpha"
        )

    assert isinstance(store, KeyringStore)
    assert not (tmp_path / "alpha").exists()
    assert list(tmp_path.iterdir()) == []


def test_keyring_store_isolates_each_backend_by_service_name(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolate_token_root(monkeypatch, tmp_path)
    service_names: list[str] = []

    class _RecordingKeyringStore:
        def __init__(self, *, service_name: str, **_kwargs: object) -> None:
            service_names.append(service_name)

    monkeypatch.setattr(soleaux.credentials, "KeyringStore", _RecordingKeyringStore)

    soleaux.credentials.build_token_store(
        _oauth_backend(token_store="keyring"), backend_name="alpha"
    )
    soleaux.credentials.build_token_store(
        _oauth_backend(token_store="keyring"), backend_name="beta"
    )

    assert service_names == ["soleaux-alpha", "soleaux-beta"]


def test_clear_token_store_reports_absence(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolate_token_root(monkeypatch, tmp_path)

    cleared = soleaux.credentials.clear_token_store(_oauth_backend(), backend_name="alpha")

    assert cleared is False


def test_clear_token_store_removes_files_but_keeps_sibling_backends(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolate_token_root(monkeypatch, tmp_path)
    backend = _oauth_backend()
    soleaux.credentials.build_token_store(backend, backend_name="alpha")
    soleaux.credentials.build_token_store(backend, backend_name="beta")
    (tmp_path / "alpha" / "token.json").write_text("{}", encoding="utf-8")
    (tmp_path / "beta" / "token.json").write_text("{}", encoding="utf-8")

    cleared = soleaux.credentials.clear_token_store(backend, backend_name="alpha")

    assert cleared is True
    assert (tmp_path / "alpha").is_dir()
    assert not (tmp_path / "alpha" / "token.json").exists()
    assert (tmp_path / "beta" / "token.json").is_file()


def test_clear_token_store_ignores_keyring_backends(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolate_token_root(monkeypatch, tmp_path)
    directory = tmp_path / "alpha"
    directory.mkdir()
    (directory / "token.json").write_text("{}", encoding="utf-8")

    cleared = soleaux.credentials.clear_token_store(
        _oauth_backend(token_store="keyring"), backend_name="alpha"
    )

    assert cleared is False
    assert (directory / "token.json").is_file()
