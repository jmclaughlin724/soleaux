"""Secure runtime policy for PostgreSQL Language Server 0.25.4.

PostgreSQL provider processes receive a small portable baseline environment
plus an exact provider allowlist. Session configuration and logs live under a
host temporary directory, never under the analyzed repository.
"""

from __future__ import annotations

import collections.abc
import dataclasses
import datetime
import hashlib
import ipaddress
import json
import os
import pathlib
import shutil
import stat
import tempfile
import time
import typing
import urllib.parse
import uuid

from pydantic import TypeAdapter

import soleaux.contracts.repository
import soleaux.postgresql.contracts

POSTGRESQL_PROVIDER_NAME: typing.Final = "postgres-language-server"
POSTGRESQL_PROVIDER_VERSION: typing.Final = "0.25.4"
POSTGRESQL_CONFIG_FILENAME: typing.Final = "postgres-language-server.jsonc"
POSTGRESQL_SCHEMA_URL: typing.Final = "https://pg-language-server.com/0.25.4/schema.json"
REDACTED_VALUE: typing.Final = "[REDACTED]"
LOG_REDACTION_CHUNK_BYTES: typing.Final = 64 * 1024
MAX_LOG_REDACTION_SECRET_BYTES: typing.Final = LOG_REDACTION_CHUNK_BYTES
_OBJECT_MAPPING_ADAPTER = TypeAdapter(dict[object, object])
_OBJECT_LIST_ADAPTER = TypeAdapter(list[object])
_OBJECT_TUPLE_ADAPTER = TypeAdapter(tuple[object, ...])

# These names are sufficient for executable discovery, locale handling,
# platform process startup, and host temporary-directory selection. Variables
# that can inject code or package-manager behavior are intentionally absent.
SAFE_BASELINE_ENVIRONMENT_NAMES: typing.Final = (
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USERPROFILE",
    "WINDIR",
)

# Exact names supported by PostgreSQL Language Server 0.25.4. This is not a
# prefix match: other PG* or PGLS* variables are not inherited.
POSTGRESQL_ENVIRONMENT_NAMES: typing.Final = (
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
GO_TOOLCHAIN_ENVIRONMENT_NAMES: typing.Final = (
    "GOCACHE",
    "GOENV",
    "GOMODCACHE",
    "GOPATH",
    "GOROOT",
)

_DATABASE_ENVIRONMENT_NAMES: typing.Final = frozenset(
    {"DATABASE_URL", "PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE"}
)
_URL_PERCENT_ENCODING_DIGITS: typing.Final = frozenset("0123456789abcdefABCDEF")
_CONFIG_TOP_LEVEL_KEYS: typing.Final = frozenset({"$schema", "db", "extends", "files"})
_DEFAULT_FILE_IGNORES: typing.Final = (
    "**/.git/**",
    "**/.soleaux-backups/**",
    "**/node_modules/**",
)


class PostgreSqlRuntimeError(RuntimeError):
    """A PostgreSQL provider runtime boundary rejected unsafe configuration."""


@dataclasses.dataclass(frozen=True, slots=True)
class PostgreSqlSessionRuntime:
    """One process-scoped PostgreSQL configuration, log path, and environment."""

    argv: tuple[str, ...]
    environment: dict[str, str]
    config_dir: pathlib.Path
    log_dir: pathlib.Path
    secret_values: tuple[str, ...]

    def cleanup(self) -> None:
        """Remove ephemeral configuration and redact retained provider logs."""
        try:
            redact_log_directory(self.log_dir, self.secret_values)
        finally:
            shutil.rmtree(self.config_dir, ignore_errors=True)


def environment_names_for_provider(provider_name: str) -> tuple[str, ...]:
    """Return the exact inherited environment-name allowlist for one provider."""
    if provider_name == POSTGRESQL_PROVIDER_NAME:
        return POSTGRESQL_ENVIRONMENT_NAMES
    if provider_name == "gopls":
        return GO_TOOLCHAIN_ENVIRONMENT_NAMES
    return ()


def uses_postgresql_runtime(environment_names: collections.abc.Sequence[str]) -> bool:
    """Whether a provider carries the exact PostgreSQL runtime allowlist."""
    return tuple(environment_names) == POSTGRESQL_ENVIRONMENT_NAMES


def capture_inherited_environment(
    environment_names: collections.abc.Sequence[str],
    inherited_environment: collections.abc.Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Capture only explicitly named variables from the inherited environment."""
    source = os.environ if inherited_environment is None else inherited_environment
    return {name: source[name] for name in environment_names if name in source}


def build_safe_environment(
    provider_environment: collections.abc.Mapping[str, str],
    *,
    environment_names: collections.abc.Sequence[str],
    inherited_environment: collections.abc.Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build the portable baseline plus only allowlisted provider variables."""
    allowed_names = frozenset(environment_names)
    unexpected_names = sorted(set(provider_environment).difference(allowed_names))
    if unexpected_names:
        names = ", ".join(unexpected_names)
        raise PostgreSqlRuntimeError(f"provider environment names are not allowlisted: {names}")
    environment = capture_inherited_environment(
        SAFE_BASELINE_ENVIRONMENT_NAMES,
        inherited_environment,
    )
    environment.update(provider_environment)
    return environment


def _decode_url_credential(value: str) -> str:
    """Decode one URL credential only when every percent escape is valid UTF-8."""
    for index, character in enumerate(value):
        if character == "%" and (
            index + 2 >= len(value)
            or any(
                digit not in _URL_PERCENT_ENCODING_DIGITS for digit in value[index + 1 : index + 3]
            )
        ):
            raise ValueError("malformed URL credential percent encoding")
    return urllib.parse.unquote(value, errors="strict")


def _parse_connection_url(
    value: str,
) -> tuple[urllib.parse.SplitResult, tuple[str, ...]]:
    """Parse one URL and return its raw and decoded userinfo components."""
    try:
        parsed = urllib.parse.urlsplit(value)
        _ = parsed.hostname, parsed.port
        encoded_credentials = (parsed.username, parsed.password)
        credentials = {
            credential
            for encoded in encoded_credentials
            if encoded is not None
            for credential in (encoded, _decode_url_credential(encoded))
            if credential
        }
    except UnicodeError, ValueError:
        raise PostgreSqlRuntimeError(
            "PostgreSQL connection URL credentials cannot be safely redacted"
        ) from None
    return parsed, tuple(credentials)


def secret_values(environment: collections.abc.Mapping[str, str]) -> tuple[str, ...]:
    """Return provider values and URL credentials in longest-first redaction order."""
    secrets = {value for value in environment.values() if value}
    connection_url = environment.get("DATABASE_URL")
    if connection_url:
        _parsed, credentials = _parse_connection_url(connection_url)
        secrets.update(credentials)
    return tuple(
        sorted(
            secrets,
            key=len,
            reverse=True,
        )
    )


def redact_text(value: str, secrets: collections.abc.Sequence[str]) -> str:
    """Replace every carried provider value before text crosses the boundary."""
    redacted = value
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, REDACTED_VALUE)
    return redacted


def redact_value(value: object, secrets: collections.abc.Sequence[str]) -> object:
    """Recursively redact provider values from errors and LSP payloads."""
    if isinstance(value, str):
        return redact_text(value, secrets)
    if isinstance(value, dict):
        mapping = _OBJECT_MAPPING_ADAPTER.validate_python(value, strict=True)
        return {
            redact_value(key, secrets): redact_value(item, secrets) for key, item in mapping.items()
        }
    if isinstance(value, list):
        items = _OBJECT_LIST_ADAPTER.validate_python(value, strict=True)
        return [redact_value(item, secrets) for item in items]
    if isinstance(value, tuple):
        items = _OBJECT_TUPLE_ADAPTER.validate_python(value, strict=True)
        return tuple(redact_value(item, secrets) for item in items)
    return value


def workspace_path_from_uri(root_uri: str) -> pathlib.Path:
    """Resolve a file URI without accepting a remote or non-file workspace."""
    try:
        decoded = soleaux.contracts.repository.file_uri_to_local_path(root_uri)
    except soleaux.contracts.repository.InvalidRepositoryPathError as exc:
        raise PostgreSqlRuntimeError("PostgreSQL provider requires a local file workspace") from exc
    path = pathlib.Path(decoded)
    if not path.is_absolute():
        raise PostgreSqlRuntimeError("PostgreSQL provider requires a local file workspace")
    return path.resolve(strict=True)


def postgresql_config(workspace_root: pathlib.Path) -> dict[str, object]:
    """Return the exact schema-valid PostgreSQL Language Server configuration."""
    workspace_root.resolve(strict=True)
    config: dict[str, object] = {
        "$schema": POSTGRESQL_SCHEMA_URL,
        "extends": [],
        "files": {
            "include": ["**/*.sql"],
            "ignore": list(_DEFAULT_FILE_IGNORES),
        },
        "db": {
            "allowStatementExecutionsAgainst": [],
        },
    }
    if frozenset(config) != _CONFIG_TOP_LEVEL_KEYS:
        raise AssertionError("PostgreSQL configuration keys drifted from the 0.25.4 schema")
    return config


def write_postgresql_config(config_dir: pathlib.Path, workspace_root: pathlib.Path) -> pathlib.Path:
    """Write one credential-free 0.25.4 JSONC configuration."""
    config_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    config_path = config_dir / POSTGRESQL_CONFIG_FILENAME
    config_path.write_text(
        f"{json.dumps(postgresql_config(workspace_root), indent=2)}\n",
        encoding="utf-8",
    )
    config_path.chmod(0o600)
    return config_path


def build_postgresql_spawn_command(
    argv: collections.abc.Sequence[str],
    config_dir: pathlib.Path,
) -> tuple[str, ...]:
    """Build the LSP command bound to an offline-safe session config."""
    if not argv:
        raise PostgreSqlRuntimeError("PostgreSQL provider command must not be empty")
    if "--skip-db" in argv:
        raise PostgreSqlRuntimeError("PostgreSQL provider command uses unsupported database flag")
    command: list[str] = []
    skip_next = False
    for token in argv:
        if skip_next:
            skip_next = False
            continue
        if token == "--config-path":
            skip_next = True
            continue
        if token.startswith("--config-path="):
            continue
        if token == "--disable-db":
            continue
        command.append(token)
    command.append(f"--config-path={config_dir}")
    return tuple(command)


def build_postgresql_validation_command(
    executable: str,
    config_dir: pathlib.Path,
) -> tuple[str, ...]:
    """Build the exact offline config-validation command for 0.25.4."""
    return (
        executable,
        "check",
        "--disable-db",
        f"--config-path={config_dir}",
    )


def require_local_connection(environment: collections.abc.Mapping[str, str]) -> None:
    """Reject explicit production-like database endpoints without exposing them."""
    connection_url = environment.get("DATABASE_URL")
    if connection_url:
        parsed, _credentials = _parse_connection_url(connection_url)
        hostname = parsed.hostname
        if parsed.scheme not in {"postgres", "postgresql"} or not _is_local_host(hostname):
            raise PostgreSqlRuntimeError(
                "PostgreSQL connected mode requires a local database endpoint"
            )
        return
    host = environment.get("PGHOST")
    if host is None:
        return
    hosts = tuple(part.strip() for part in host.split(","))
    if not hosts or any(not _is_local_host(part) for part in hosts):
        raise PostgreSqlRuntimeError("PostgreSQL connected mode requires a local database endpoint")


def create_postgresql_session_runtime(
    *,
    argv: collections.abc.Sequence[str],
    workspace_root: pathlib.Path,
    provider_environment: collections.abc.Mapping[str, str],
    logs_retention_days: int,
    temp_retention_hours: int,
    runtime_root: pathlib.Path | None = None,
    inherited_environment: collections.abc.Mapping[str, str] | None = None,
) -> PostgreSqlSessionRuntime:
    """Create a session runtime that is offline unless local DB env is explicit."""
    require_local_connection(provider_environment)
    root = workspace_root.resolve(strict=True)
    base = (
        runtime_root.resolve()
        if runtime_root is not None
        else (pathlib.Path(tempfile.gettempdir()) / "soleaux" / "postgresql").resolve()
    )
    if _is_within(base, root):
        raise PostgreSqlRuntimeError("PostgreSQL runtime directory must be outside the workspace")
    config_root = base / "config"
    log_root = base / "logs"
    config_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    log_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    cleanup_expired_session_directories(
        config_root,
        max_age_seconds=temp_retention_hours * 60 * 60,
    )
    cleanup_expired_session_directories(
        log_root,
        max_age_seconds=logs_retention_days * 24 * 60 * 60,
    )

    session_name = f"session-{uuid.uuid4().hex}"
    config_dir = config_root / session_name
    log_dir = log_root / session_name
    write_postgresql_config(config_dir, root)
    log_dir.mkdir(mode=0o700)

    carried_environment = dict(provider_environment)
    carried_secrets = secret_values(carried_environment)
    environment = build_safe_environment(
        carried_environment,
        environment_names=POSTGRESQL_ENVIRONMENT_NAMES,
        inherited_environment=inherited_environment,
    )
    environment["PGLS_CONFIG_PATH"] = str(config_dir)
    environment["PGLS_LOG_PATH"] = str(log_dir)
    spawn_argv = build_postgresql_spawn_command(argv, config_dir)
    return PostgreSqlSessionRuntime(
        argv=spawn_argv,
        environment=environment,
        config_dir=config_dir,
        log_dir=log_dir,
        secret_values=carried_secrets,
    )


def cleanup_expired_session_directories(
    root: pathlib.Path,
    *,
    max_age_seconds: int,
    now: float | None = None,
) -> tuple[pathlib.Path, ...]:
    """Remove only stale, non-symlink session directories under one runtime root."""
    if max_age_seconds <= 0:
        raise ValueError("session retention must be positive")
    if not root.is_dir():
        return ()
    current_time = time.time() if now is None else now
    removed: list[pathlib.Path] = []
    for candidate in root.iterdir():
        if (
            not candidate.name.startswith("session-")
            or candidate.is_symlink()
            or not candidate.is_dir()
        ):
            continue
        try:
            modified_at = candidate.stat().st_mtime
        except OSError:
            continue
        if current_time - modified_at <= max_age_seconds:
            continue
        shutil.rmtree(candidate)
        removed.append(candidate)
    return tuple(removed)


def _retained_secret_prefix_length(
    value: bytes,
    secrets: collections.abc.Sequence[bytes],
) -> int:
    """Return the longest suffix that may complete a secret in the next chunk."""
    retained = 0
    for secret in secrets:
        candidate_limit = min(len(value), len(secret) - 1)
        for length in range(candidate_limit, retained, -1):
            if value.endswith(secret[:length]):
                retained = length
                break
    return retained


def _redact_log_bytes(
    value: bytes,
    secrets: collections.abc.Sequence[bytes],
    *,
    final: bool,
) -> tuple[bytes, bytes, bool]:
    """Redact complete secrets and retain only a possible cross-chunk prefix."""
    output = bytearray()
    cursor = 0
    changed = False
    while cursor < len(value):
        matches = (
            (offset, -len(secret), secret)
            for secret in secrets
            if (offset := value.find(secret, cursor)) >= 0
        )
        match = min(matches, default=None)
        if match is None:
            remainder = value[cursor:]
            retained = 0 if final else _retained_secret_prefix_length(remainder, secrets)
            stable_end = len(remainder) - retained
            output.extend(remainder[:stable_end])
            return bytes(output), remainder[stable_end:], changed
        offset, _negated_length, secret = match
        output.extend(value[cursor:offset])
        output.extend(REDACTED_VALUE.encode())
        cursor = offset + len(secret)
        changed = True
    return bytes(output), b"", changed


def _remove_unredactable_log(path: pathlib.Path) -> None:
    """Remove one retained log that could not be redacted safely."""
    try:
        path.unlink(missing_ok=True)
    except OSError as error:
        raise PostgreSqlRuntimeError(
            f"could not redact or remove retained provider log: {path.name}"
        ) from error


def _remove_unredactable_log_tree(log_dir: pathlib.Path) -> None:
    """Remove a retained log tree whose contents cannot be proven secret-free."""
    try:
        shutil.rmtree(log_dir)
    except FileNotFoundError:
        return
    except OSError as error:
        raise PostgreSqlRuntimeError(
            "could not redact or remove retained provider log directory"
        ) from error


def _stream_contains_secret(
    source: typing.BinaryIO,
    secrets: collections.abc.Sequence[bytes],
) -> bool:
    """Verify a stream contains no secret, including across chunk boundaries."""
    longest_secret = max(len(secret) for secret in secrets)
    retained = b""
    while chunk := source.read(LOG_REDACTION_CHUNK_BYTES):
        candidate = retained + chunk
        if any(secret in candidate for secret in secrets):
            return True
        retained = candidate[-(longest_secret - 1) :] if longest_secret > 1 else b""
    return False


def _redact_log_file(path: pathlib.Path, secrets: collections.abc.Sequence[bytes]) -> None:
    """Stream one log through an owner-only atomic replacement."""
    source_fd = -1
    temporary_fd = -1
    temporary_path: pathlib.Path | None = None
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        source_fd = os.open(path, flags)
        source_stat = os.fstat(source_fd)
        if not stat.S_ISREG(source_stat.st_mode):
            os.close(source_fd)
            source_fd = -1
            return
        temporary_fd, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".redacted",
            dir=path.parent,
        )
        temporary_path = pathlib.Path(temporary_name)
        os.fchmod(temporary_fd, 0o600)
        pending = b""
        changed = False
        with (
            os.fdopen(source_fd, "rb") as source,
            os.fdopen(temporary_fd, "w+b") as target,
        ):
            source_fd = -1
            temporary_fd = -1
            while chunk := source.read(LOG_REDACTION_CHUNK_BYTES):
                stable, pending, chunk_changed = _redact_log_bytes(
                    pending + chunk,
                    secrets,
                    final=False,
                )
                target.write(stable)
                changed = changed or chunk_changed
            stable, pending, chunk_changed = _redact_log_bytes(
                pending,
                secrets,
                final=True,
            )
            target.write(stable)
            target.write(pending)
            changed = changed or chunk_changed
            target.flush()
            os.fsync(target.fileno())
            target.seek(0)
            collision = _stream_contains_secret(target, secrets)
        if collision:
            _remove_unredactable_log(path)
            return
        if changed:
            temporary_path.replace(path)
            temporary_path = None
    except OSError:
        _remove_unredactable_log(path)
    finally:
        if source_fd >= 0:
            os.close(source_fd)
        if temporary_fd >= 0:
            os.close(temporary_fd)
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def redact_log_directory(log_dir: pathlib.Path, secrets: collections.abc.Sequence[str]) -> None:
    """Redact retained provider logs with bounded binary processing."""
    try:
        root_stat = log_dir.lstat()
    except FileNotFoundError:
        return
    except OSError:
        _remove_unredactable_log_tree(log_dir)
        return
    if not stat.S_ISDIR(root_stat.st_mode):
        _remove_unredactable_log(log_dir)
        return
    if not secrets:
        return
    try:
        encoded_secrets = tuple(
            sorted(
                {os.fsencode(secret) for secret in secrets if secret},
                key=len,
                reverse=True,
            )
        )
    except UnicodeError:
        _remove_unredactable_log_tree(log_dir)
        return
    if not encoded_secrets:
        return
    redaction_is_bounded = all(
        len(secret) <= MAX_LOG_REDACTION_SECRET_BYTES for secret in encoded_secrets
    )
    traversal_errors: list[OSError] = []
    try:
        for directory, _subdirectories, filenames in os.walk(
            log_dir,
            followlinks=False,
            onerror=traversal_errors.append,
        ):
            for filename in sorted(filenames):
                path = pathlib.Path(directory) / filename
                try:
                    entry_stat = path.lstat()
                except OSError as error:
                    traversal_errors.append(error)
                    continue
                if stat.S_ISLNK(entry_stat.st_mode) or not stat.S_ISREG(entry_stat.st_mode):
                    continue
                if redaction_is_bounded:
                    _redact_log_file(path, encoded_secrets)
                else:
                    _remove_unredactable_log(path)
    except PostgreSqlRuntimeError:
        _remove_unredactable_log_tree(log_dir)
        return
    if traversal_errors:
        _remove_unredactable_log_tree(log_dir)


def connected_state_disclosure(
    database_state: bytes,
    *,
    observed_at: datetime.datetime,
) -> soleaux.postgresql.contracts.ExternalStateDisclosure:
    """Disclose connected-state dependence through the frozen opaque contract."""
    return soleaux.postgresql.contracts.ExternalStateDisclosure(
        status=soleaux.postgresql.contracts.ExternalStateStatus.CONSULTED,
        database_state_fingerprint=hashlib.sha256(database_state).hexdigest(),
        observed_at=observed_at,
    )


def database_environment(environment: collections.abc.Mapping[str, str]) -> dict[str, str]:
    """Return carried database variables without widening the public allowlist."""
    return {
        name: value for name, value in environment.items() if name in _DATABASE_ENVIRONMENT_NAMES
    }


def _is_local_host(host: str | None) -> bool:
    if host is None or host == "" or host.startswith("/"):
        return True
    normalized = host.removesuffix(".").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _is_within(path: pathlib.Path, root: pathlib.Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
