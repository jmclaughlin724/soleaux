"""Secure PostgreSQL provider environment, config, and session runtime."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from _assertions import raises_with_message

from soleaux.postgresql import contracts
from soleaux.postgresql import runtime as postgresql_runtime


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "database" / "schema").mkdir(parents=True)
    (workspace / "history").mkdir()
    (workspace / "checks").mkdir()
    return workspace


def _contains_postgresql_url_credential(value: str) -> bool:
    for scheme in ("postgres://", "postgresql://"):
        offset = 0
        while (start := value.find(scheme, offset)) >= 0:
            authority_start = start + len(scheme)
            authority_end = authority_start
            while authority_end < len(value) and (
                not value[authority_end].isspace()
                and value[authority_end] not in {"/", '"', "'", "`"}
            ):
                authority_end += 1
            authority = value[authority_start:authority_end]
            userinfo, separator, _host = authority.rpartition("@")
            if separator and ":" in userinfo:
                return True
            offset = authority_start
    return False


def test_d1_d7_d8_d9_provider_environment_names_are_exact() -> None:
    assert postgresql_runtime.POSTGRESQL_ENVIRONMENT_NAMES == (
        "DATABASE_URL",
        "PGHOST",
        "PGPORT",
        "PGUSER",
        "PGPASSWORD",
        "PGDATABASE",
        "PGLS_CONFIG_PATH",
        "PGLS_LOG_PATH",
        "PGLS_LOG_LEVEL",
        "PGLS_LOG_PREFIX_NAME",
        "PGLS_LOG_KIND",
    )
    assert (
        postgresql_runtime.environment_names_for_provider("postgres-language-server")
        == postgresql_runtime.POSTGRESQL_ENVIRONMENT_NAMES
    )
    assert (
        postgresql_runtime.environment_names_for_provider("gopls")
        == postgresql_runtime.GO_TOOLCHAIN_ENVIRONMENT_NAMES
    )
    assert postgresql_runtime.environment_names_for_provider("pyright-langserver") == ()


def test_d3_d4_safe_environment_inherits_only_baseline_and_allowed_names() -> None:
    inherited = {
        "PATH": "/safe/bin",
        "LANG": "C.UTF-8",
        "DATABASE_URL": "postgresql://ignored.example/db",
        "PGSSLMODE": "require",
        "PYTHONPATH": "/inject",
        "AWS_SECRET_ACCESS_KEY": "cloud-secret",
    }
    provider_environment = {
        "DATABASE_URL": "postgresql://reader:session-secret@127.0.0.1/local",
        "PGLS_LOG_KIND": "json",
    }

    environment = postgresql_runtime.build_safe_environment(
        provider_environment,
        environment_names=postgresql_runtime.POSTGRESQL_ENVIRONMENT_NAMES,
        inherited_environment=inherited,
    )

    assert environment == {
        "PATH": "/safe/bin",
        "LANG": "C.UTF-8",
        **provider_environment,
    }
    assert "PGSSLMODE" not in environment
    assert "PYTHONPATH" not in environment
    assert "AWS_SECRET_ACCESS_KEY" not in environment


def test_d4_rejects_provider_variable_outside_the_exact_allowlist() -> None:
    with raises_with_message(postgresql_runtime.PostgreSqlRuntimeError, "PGSSLMODE"):
        postgresql_runtime.build_safe_environment(
            {"PGSSLMODE": "require"},
            environment_names=postgresql_runtime.POSTGRESQL_ENVIRONMENT_NAMES,
            inherited_environment={},
        )


@pytest.mark.parametrize(
    ("database_url", "credential_renderings"),
    [
        pytest.param(
            "postgresql://raw-user:raw-password@localhost/local",
            ("raw-user", "raw-password"),
            id="raw",
        ),
        pytest.param(
            "postgresql://encoded%40user:encoded%3Apassword@localhost/local",
            (
                "encoded%40user",
                "encoded@user",
                "encoded%3Apassword",
                "encoded:password",
            ),
            id="percent-encoded",
        ),
    ],
)
def test_retained_log_redaction_covers_connection_url_credentials(
    tmp_path: Path,
    database_url: str,
    credential_renderings: tuple[str, ...],
) -> None:
    secrets = postgresql_runtime.secret_values({"DATABASE_URL": database_url})

    assert database_url in secrets
    assert set(credential_renderings).issubset(secrets)
    assert [len(secret) for secret in secrets] == sorted(
        (len(secret) for secret in secrets),
        reverse=True,
    )

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_path = log_dir / "provider.log"
    log_path.write_text("\n".join(credential_renderings), encoding="utf-8")

    postgresql_runtime.redact_log_directory(log_dir, secrets)

    retained = log_path.read_text(encoding="utf-8")
    assert all(rendering not in retained for rendering in credential_renderings)
    assert postgresql_runtime.REDACTED_VALUE in retained


@pytest.mark.parametrize(
    "database_url",
    [
        pytest.param(
            "postgresql://reader:malformed%escape@localhost/local",
            id="malformed-percent-escape",
        ),
        pytest.param(
            "postgresql://reader:invalid%FFutf8@localhost/local",
            id="invalid-utf8-escape",
        ),
        pytest.param(
            "postgresql://reader:password@[::1/local",
            id="malformed-authority",
        ),
    ],
)
def test_secret_values_rejects_uncoverable_postgresql_urls(database_url: str) -> None:
    with pytest.raises(postgresql_runtime.PostgreSqlRuntimeError) as caught:
        postgresql_runtime.secret_values({"DATABASE_URL": database_url})

    assert "cannot be safely redacted" in str(caught.value)
    assert database_url not in str(caught.value)


def test_d12_d13_d14_d15_d17_generated_config_uses_only_0254_schema_keys(
    tmp_path: Path,
) -> None:
    config = postgresql_runtime.postgresql_config(_workspace(tmp_path))

    assert set(config) == {"$schema", "db", "extends", "files"}
    assert config["$schema"] == "https://pg-language-server.com/0.25.4/schema.json"
    assert config["extends"] == []
    assert config["files"] == {
        "include": ["**/*.sql"],
        "ignore": [
            "**/.git/**",
            "**/.soleaux-backups/**",
            "**/node_modules/**",
        ],
    }
    assert config["db"] == {"allowStatementExecutionsAgainst": []}
    assert "database" not in config
    assert "lanes" not in config
    assert "disableConnection" not in json.dumps(config)


def test_d10_d11_d16_session_paths_environment_command_and_cleanup(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    runtime_root = tmp_path / "runtime"
    database_url = "postgresql://reader:session-secret@127.0.0.1/local"

    session = postgresql_runtime.create_postgresql_session_runtime(
        argv=("postgres-language-server", "lsp-proxy"),
        workspace_root=workspace,
        provider_environment={
            "DATABASE_URL": database_url,
            "PGLS_LOG_LEVEL": "debug",
            "PGLS_LOG_KIND": "json",
        },
        logs_retention_days=7,
        temp_retention_hours=24,
        runtime_root=runtime_root,
        inherited_environment={
            "PATH": "/safe/bin",
            "AWS_SECRET_ACCESS_KEY": "must-not-propagate",
        },
    )

    config_path = session.config_dir / postgresql_runtime.POSTGRESQL_CONFIG_FILENAME
    assert config_path.is_file()
    assert not session.config_dir.is_relative_to(workspace)
    assert not session.log_dir.is_relative_to(workspace)
    assert session.environment["DATABASE_URL"] == database_url
    assert session.environment["PGLS_CONFIG_PATH"] == str(session.config_dir)
    assert session.environment["PGLS_LOG_PATH"] == str(session.log_dir)
    assert "AWS_SECRET_ACCESS_KEY" not in session.environment
    assert session.argv == (
        "postgres-language-server",
        "lsp-proxy",
        f"--config-path={session.config_dir}",
    )

    log_path = session.log_dir / "provider.log"
    log_path.write_text(f"failed to connect with {database_url}\n", encoding="utf-8")
    session.cleanup()

    assert not session.config_dir.exists()
    assert session.log_dir.is_dir()
    assert database_url not in log_path.read_text(encoding="utf-8")
    assert postgresql_runtime.REDACTED_VALUE in log_path.read_text(encoding="utf-8")


def test_retained_log_redaction_is_bounded_binary_and_cross_chunk_safe(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_path = log_dir / "provider.log"
    secret = "postgresql://reader:cross-chunk-secret@127.0.0.1/local"
    chunk_size = postgresql_runtime.LOG_REDACTION_CHUNK_BYTES
    prefix = b"\xff" + (b"x" * (chunk_size - 3))
    suffix = b"y" * ((chunk_size * 2) + 17)
    log_path.write_bytes(prefix + secret.encode() + suffix)
    original_inode = log_path.stat().st_ino

    postgresql_runtime.redact_log_directory(log_dir, (secret,))

    retained = log_path.read_bytes()
    assert secret.encode() not in retained
    assert postgresql_runtime.REDACTED_VALUE.encode() in retained
    assert retained.startswith(b"\xff")
    assert retained.endswith(suffix)
    assert len(retained) > chunk_size * 2
    assert log_path.stat().st_ino != original_inode
    assert log_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    ("secrets", "content"),
    [
        (("REDACTED",), b"REDACTED"),
        (("foo", "x["), b"xfoo"),
    ],
)
def test_retained_log_redaction_removes_marker_collisions(
    tmp_path: Path,
    secrets: tuple[str, ...],
    content: bytes,
) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_path = log_dir / "provider.log"
    log_path.write_bytes(content)

    postgresql_runtime.redact_log_directory(log_dir, secrets)

    assert not log_path.exists()


@pytest.mark.skipif(os.name != "posix", reason="surrogateescape is a POSIX environment contract")
def test_retained_log_redaction_handles_surrogate_escaped_environment_bytes(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_path = log_dir / "provider.log"
    secret_bytes = b"\xffraw-environment-secret"
    secret = os.fsdecode(secret_bytes)
    log_path.write_bytes(b"failure: " + secret_bytes)

    postgresql_runtime.redact_log_directory(log_dir, (secret,))

    retained = log_path.read_bytes()
    assert secret_bytes not in retained
    assert postgresql_runtime.REDACTED_VALUE.encode() in retained


def test_retained_log_is_removed_when_secret_exceeds_the_redaction_bound(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_path = log_dir / "provider.log"
    secret = "s" * (postgresql_runtime.MAX_LOG_REDACTION_SECRET_BYTES + 1)
    log_path.write_bytes(b"before-" + secret.encode() + b"-after")

    postgresql_runtime.redact_log_directory(log_dir, (secret,))

    assert not log_path.exists()


def test_retained_log_is_removed_when_secure_replacement_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_path = log_dir / "provider.log"
    secret = "postgresql://reader:replacement-secret@127.0.0.1/local"
    log_path.write_text(f"failed with {secret}", encoding="utf-8")
    original_replace = Path.replace

    def fail_log_replacement(source: Path, target: Path) -> Path:
        if source.suffix == ".redacted" and target == log_path:
            raise OSError("injected replacement failure")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_log_replacement)

    postgresql_runtime.redact_log_directory(log_dir, (secret,))

    assert not log_path.exists()
    assert tuple(log_dir.iterdir()) == ()


def test_retained_log_redaction_removes_tree_after_traversal_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_dir = tmp_path / "logs"
    nested = log_dir / "nested"
    nested.mkdir(parents=True)
    (nested / "provider.log").write_text("retained-secret", encoding="utf-8")

    def failed_walk(
        _root: Path,
        *,
        followlinks: bool,
        onerror: object,
    ) -> tuple[()]:
        assert followlinks is False
        assert callable(onerror)
        onerror(PermissionError("retained log traversal failed"))
        return ()

    monkeypatch.setattr(postgresql_runtime.os, "walk", failed_walk)

    postgresql_runtime.redact_log_directory(log_dir, ("retained-secret",))

    assert not log_dir.exists()


def test_retained_log_redaction_removes_tree_after_entry_metadata_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_path = log_dir / "provider.log"
    log_path.write_text("retained-secret", encoding="utf-8")
    original_lstat = Path.lstat

    def failed_log_lstat(path: Path) -> os.stat_result:
        if path == log_path:
            raise PermissionError("retained log metadata failed")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", failed_log_lstat)

    postgresql_runtime.redact_log_directory(log_dir, ("retained-secret",))

    assert not log_dir.exists()


def test_retained_log_redaction_removes_tree_after_root_metadata_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "provider.log").write_text("retained-secret", encoding="utf-8")
    original_lstat = Path.lstat

    def failed_root_lstat(path: Path) -> os.stat_result:
        if path == log_dir:
            raise PermissionError("retained log root metadata failed")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", failed_root_lstat)

    postgresql_runtime.redact_log_directory(log_dir, ("retained-secret",))

    assert not log_dir.exists()


def test_retained_log_redaction_removes_unexpected_regular_file_root(
    tmp_path: Path,
) -> None:
    log_root = tmp_path / "logs"
    log_root.write_text("unexpected retained log root", encoding="utf-8")

    postgresql_runtime.redact_log_directory(log_root, ())

    assert not log_root.exists()


def test_retained_log_redaction_unlinks_symlink_root_without_following_it(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    retained_log = target / "provider.log"
    retained_log.write_text("target must remain untouched", encoding="utf-8")
    log_root = tmp_path / "logs"
    log_root.symlink_to(target, target_is_directory=True)

    postgresql_runtime.redact_log_directory(log_root, ())

    assert not log_root.is_symlink()
    assert retained_log.read_text(encoding="utf-8") == "target must remain untouched"


def test_d16_lsp_spawn_omits_check_only_disable_db(tmp_path: Path) -> None:
    assert postgresql_runtime.build_postgresql_spawn_command(
        (
            "postgres-language-server",
            "lsp-proxy",
            "--disable-db",
            "--config-path",
            "/stale/config",
        ),
        tmp_path,
    ) == (
        "postgres-language-server",
        "lsp-proxy",
        f"--config-path={tmp_path}",
    )


def test_d11_cleanup_removes_only_expired_session_directories(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    stale = runtime_root / "session-stale"
    current = runtime_root / "session-current"
    unrelated = runtime_root / "keep"
    for directory in (stale, current, unrelated):
        directory.mkdir(parents=True)
    now = 1_000_000.0
    os.utime(stale, (now - 101, now - 101))
    os.utime(current, (now - 99, now - 99))
    os.utime(unrelated, (now - 1_000, now - 1_000))

    removed = postgresql_runtime.cleanup_expired_session_directories(
        runtime_root,
        max_age_seconds=100,
        now=now,
    )

    assert removed == (stale,)
    assert not stale.exists()
    assert current.is_dir()
    assert unrelated.is_dir()


@pytest.mark.parametrize(
    "environment",
    [
        {"DATABASE_URL": "postgresql://reader:secret@database.example.com/prod"},
        {"PGHOST": "10.20.30.40", "PGPASSWORD": "secret"},
        {"PGHOST": "127.0.0.1,database.example.com", "PGPASSWORD": "secret"},
    ],
)
def test_d18_production_like_endpoints_are_rejected_without_secret(
    environment: dict[str, str],
) -> None:
    with pytest.raises(postgresql_runtime.PostgreSqlRuntimeError) as caught:
        postgresql_runtime.require_local_connection(environment)

    assert "secret" not in str(caught.value)
    assert "database.example.com" not in str(caught.value)
    assert "10.20.30.40" not in str(caught.value)


@pytest.mark.parametrize(
    "environment",
    [
        {},
        {"DATABASE_URL": "postgresql:///local"},
        {"DATABASE_URL": "postgresql://reader:secret@localhost/local"},
        {"DATABASE_URL": "postgresql://reader:secret@[::1]/local"},
        {"PGHOST": "/var/run/postgresql"},
        {"PGHOST": "127.0.0.1,::1"},
    ],
)
def test_d18_local_endpoints_are_accepted(environment: dict[str, str]) -> None:
    postgresql_runtime.require_local_connection(environment)


def test_d21_validation_command_uses_disable_db_and_explicit_config_dir(
    tmp_path: Path,
) -> None:
    assert postgresql_runtime.build_postgresql_validation_command(
        "/installed/postgres-language-server",
        tmp_path,
    ) == (
        "/installed/postgres-language-server",
        "check",
        "--disable-db",
        f"--config-path={tmp_path}",
    )


def test_d6_d20_d22_connected_disclosure_hashes_state_and_exposes_no_secret() -> None:
    secret = "postgresql://reader:session-secret@127.0.0.1/local"
    disclosure = postgresql_runtime.connected_state_disclosure(
        f"catalog rows from {secret}".encode(),
        observed_at=datetime(2026, 7, 25, tzinfo=UTC),
    )
    payload = disclosure.model_dump_json()

    assert isinstance(disclosure, contracts.ExternalStateDisclosure)
    assert disclosure.status is contracts.ExternalStateStatus.CONSULTED
    assert disclosure.source_authority == "repository_source"
    assert secret not in payload
    assert "session-secret" not in payload
    assert len(disclosure.database_state_fingerprint or "") == 64


def test_d6_d22_redacts_errors_logs_evidence_metadata_and_diagnostics() -> None:
    secret = "postgresql://reader:surface-secret@127.0.0.1/local"
    payload = {
        "error": f"connection failed: {secret}",
        "logs": [secret],
        "evidence": {"provider": secret},
        "metadata": (secret,),
        "diagnostics": [{"message": secret}],
    }

    redacted = postgresql_runtime.redact_value(payload, (secret,))
    rendered = json.dumps(redacted)

    assert secret not in rendered
    assert "surface-secret" not in rendered
    assert rendered.count(postgresql_runtime.REDACTED_VALUE) == 5


def test_d23_runtime_and_guide_commit_no_connection_credential() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src" / "soleaux"
    surfaces = (
        source_root / "postgresql" / "runtime.py",
        source_root / "resources" / "docs" / "postgresql-security.md",
    )
    for surface in surfaces:
        content = surface.read_text(encoding="utf-8")
        assert not _contains_postgresql_url_credential(content)
