"""soleaux.toml resolution and gateway policy validation (D021, D034, D036)."""

import pathlib

import _assertions
import _host_root
import pydantic
import pytest

import soleaux.contracts.config
import soleaux.postgresql.contracts

REPOSITORY_ROOT = _host_root.require_host_root()


def _mcp_model(**overrides: object) -> soleaux.contracts.config.McpBackendConfig:
    payload: dict[str, object] = {"command": ["backend"]}
    payload.update(overrides)
    return soleaux.contracts.config.McpBackendConfig.model_validate(payload)


def test_absent_config_resolves_to_the_complete_default(tmp_path: pathlib.Path) -> None:
    resolved = soleaux.contracts.config.load_config(tmp_path)
    assert resolved == soleaux.contracts.config.ResolvedConfig.default()


def test_empty_and_schema_only_configs_resolve_to_the_same_default(tmp_path: pathlib.Path) -> None:
    (tmp_path / "soleaux.toml").write_bytes(b"")
    empty = soleaux.contracts.config.load_config(tmp_path)
    (tmp_path / "soleaux.toml").write_text("# only a comment\n", encoding="utf-8")
    comments = soleaux.contracts.config.load_config(tmp_path)
    assert empty.model_dump(
        mode="json"
    ) == soleaux.contracts.config.ResolvedConfig.default().model_dump(mode="json")
    assert comments.model_dump(mode="json") == empty.model_dump(mode="json")


def test_unknown_keys_fail_clearly(tmp_path: pathlib.Path) -> None:
    (tmp_path / "soleaux.toml").write_text("[bogus]\nkey = 1\n", encoding="utf-8")
    with _assertions.raises_with_message(soleaux.contracts.config.ConfigError, "bogus"):
        soleaux.contracts.config.load_config(tmp_path)


def test_invalid_toml_fails_clearly(tmp_path: pathlib.Path) -> None:
    (tmp_path / "soleaux.toml").write_text("not = [toml\n", encoding="utf-8")
    with _assertions.raises_with_message(soleaux.contracts.config.ConfigError, "invalid TOML"):
        soleaux.contracts.config.load_config(tmp_path)


def test_workspaces_and_providers_round_trip(tmp_path: pathlib.Path) -> None:
    (tmp_path / "soleaux.toml").write_text(
        '[[workspaces]]\nid = "main"\nroot = "."\n\n[providers.ty]\ncommand = ["ty", "server"]\n',
        encoding="utf-8",
    )
    resolved = soleaux.contracts.config.load_config(tmp_path)
    assert resolved.workspaces[0].id == "main"
    assert resolved.providers["ty"].command == ["ty", "server"]
    assert resolved.providers["ty"].enabled is True


def test_lsp_diagnostic_timeout_defaults_and_round_trips(tmp_path: pathlib.Path) -> None:
    assert soleaux.contracts.config.ResolvedConfig.default().lsp.diagnostic_timeout_seconds == 5.0
    (tmp_path / "soleaux.toml").write_text(
        "[lsp]\ndiagnostic_timeout_seconds = 1.25\n",
        encoding="utf-8",
    )

    resolved = soleaux.contracts.config.load_config(tmp_path)

    assert resolved.lsp.diagnostic_timeout_seconds == 1.25
    assert resolved.public_payload()["lsp"] == {"diagnostic_timeout_seconds": 1.25}


def test_postgresql_lanes_require_explicit_repository_evidence(
    tmp_path: pathlib.Path,
) -> None:
    assert soleaux.contracts.config.ResolvedConfig.default().postgresql.lane_roots == {}
    (tmp_path / "soleaux.toml").write_text(
        "\n".join(
            (
                "[postgresql.lane_roots]",
                'desired_state = ["database/schema"]',
                'migration_history = ["database/migrations"]',
                "",
            )
        ),
        encoding="utf-8",
    )

    resolved = soleaux.contracts.config.load_config(tmp_path)

    assert resolved.postgresql.lane_roots == {
        soleaux.postgresql.contracts.SourceLane.DESIRED_STATE: ("database/schema",),
        soleaux.postgresql.contracts.SourceLane.MIGRATION_HISTORY: ("database/migrations",),
    }


@pytest.mark.parametrize(
    "root",
    ["/database/schema", "../schema", "database/\0schema"],
)
def test_postgresql_lane_roots_reject_uncontained_paths(
    tmp_path: pathlib.Path,
    root: str,
) -> None:
    (tmp_path / "soleaux.toml").write_text(
        f'[postgresql.lane_roots]\ndesired_state = ["{root}"]\n',
        encoding="utf-8",
    )

    with pytest.raises(soleaux.contracts.config.ConfigError):
        soleaux.contracts.config.load_config(tmp_path)


def test_coverage_artifacts_require_explicit_contained_local_configuration(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "soleaux.toml").write_text(
        "\n".join(
            (
                "[[coverage.artifacts]]",
                'path = "ci/coverage.json"',
                'format = "soleaux_json"',
                "",
            )
        ),
        encoding="utf-8",
    )

    resolved = soleaux.contracts.config.load_config(tmp_path)

    assert resolved.coverage.artifacts == (
        soleaux.contracts.config.CoverageArtifactConfig(
            path="ci/coverage.json",
            format="soleaux_json",
        ),
    )
    assert "coverage" not in soleaux.contracts.config.ResolvedConfig.default().public_payload()


@pytest.mark.parametrize("value", [0, 60.001, float("nan"), float("inf")])
def test_lsp_diagnostic_timeout_rejects_invalid_values(value: float) -> None:
    with pytest.raises(pydantic.ValidationError):
        soleaux.contracts.config.LspConfig(diagnostic_timeout_seconds=value)


@pytest.mark.parametrize(
    "package",
    (
        "../parser",
        "./parser",
        "/parser",
        "node:fs",
        "package/subpath",
        "@scope/package/subpath",
    ),
)
def test_structural_language_packages_reject_paths(package: str) -> None:
    with _assertions.raises_with_message(pydantic.ValidationError, "dynamic language package"):
        soleaux.contracts.config.StructuralConfig(
            backend="napi",
            languages={"Configured": package},
        )


def test_structural_language_packages_accept_bare_package_identities() -> None:
    config = soleaux.contracts.config.StructuralConfig(
        backend="napi",
        languages={
            "Json": "@ast-grep/lang-json",
            "Custom": "custom-language",
        },
    )

    assert config.languages == {
        "Json": "@ast-grep/lang-json",
        "Custom": "custom-language",
    }


def test_repository_mcp_config_parses_without_executing_backends() -> None:
    resolved = soleaux.contracts.config.load_config(REPOSITORY_ROOT)

    assert set(resolved.mcp) == {
        "anilize",
        "context7",
        "eslint",
        "next-devtools",
        "openai-docs",
        "playwright",
        "shadcn",
        "supabase-anilize-temp",
        "web-reader",
        "web-search-prime",
        "zod",
        "zread",
    }
    command_backends = {"eslint", "next-devtools", "playwright", "shadcn"}
    for name, backend in resolved.mcp.items():
        if name in command_backends:
            assert backend.command is not None
        else:
            assert backend.url is not None
    assert resolved.mcp["playwright"].lifecycle == "session"
    assert resolved.mcp["supabase-anilize-temp"].auth == "oauth"
    assert (
        resolved.mcp["supabase-anilize-temp"].oauth_token_endpoint_auth_method
        == "client_secret_post"
    )
    assert resolved.mcp["zod"].auth == "none"
    assert resolved.mcp["zread"].auth == "bearer_env"
    assert resolved.mcp["zread"].auth_token_env == "ZAI_API_KEY"


def test_mcp_command_config_preserves_explicit_values() -> None:
    backend = _mcp_model(
        command=["backend", "$(literal-argument)"],
        env={"MODE": "test"},
        cwd="packages/app",
    )

    assert backend.command == ["backend", "$(literal-argument)"]
    assert backend.env == {"MODE": "test"}
    assert backend.cwd == "packages/app"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        pytest.param({}, "exactly one", id="missing-source"),
        pytest.param(
            {"command": ["backend"], "url": "https://example.com/mcp"},
            "exactly one",
            id="both-sources",
        ),
        pytest.param({"command": []}, "nonempty", id="empty-command"),
        pytest.param({"command": [""]}, "nonempty", id="empty-command-part"),
        pytest.param({"command": ["bad\x00command"]}, "NUL-free", id="nul-command"),
        pytest.param(
            {"command": ["backend"], "env": {"INVALID-NAME": "value"}},
            "valid names",
            id="invalid-env-name",
        ),
        pytest.param(
            {"command": ["backend"], "env": {"VALID_NAME": "bad\x00value"}},
            "NUL-free",
            id="nul-env-value",
        ),
        pytest.param(
            {"command": ["backend"], "cwd": ""},
            "contained relative path",
            id="empty-cwd",
        ),
        pytest.param(
            {"command": ["backend"], "cwd": "../outside"},
            "contained relative path",
            id="parent-cwd",
        ),
        pytest.param(
            {"command": ["backend"], "cwd": "/absolute"},
            "contained relative path",
            id="absolute-cwd",
        ),
    ],
)
def test_mcp_source_command_env_and_cwd_validation(
    payload: dict[str, object], message: str
) -> None:
    with _assertions.raises_with_message(pydantic.ValidationError, message):
        soleaux.contracts.config.McpBackendConfig.model_validate(payload)


def test_mcp_namespaces_accept_stable_kebab_and_snake_case() -> None:
    resolved = soleaux.contracts.config.ResolvedConfig(
        mcp={
            "next-devtools": _mcp_model(),
            "snake_case": _mcp_model(),
        }
    )

    assert set(resolved.mcp) == {"next-devtools", "snake_case"}


@pytest.mark.parametrize(
    "namespace",
    [
        pytest.param("soleaux", id="reserved"),
        pytest.param("local", id="reserved-metrics-sentinel"),
        pytest.param("telemetry", id="reserved-telemetry-prefix"),
        pytest.param("Uppercase", id="uppercase"),
        pytest.param("-leading", id="leading-separator"),
        pytest.param("trailing-", id="trailing-separator"),
        pytest.param("double__separator", id="double-separator"),
        pytest.param("contains.dot", id="dot"),
        pytest.param("contains/slash", id="slash"),
        pytest.param("contains space", id="space"),
    ],
)
def test_mcp_namespace_syntax_and_reserved_name_are_rejected(namespace: str) -> None:
    with _assertions.raises_with_message(
        pydantic.ValidationError, "invalid or reserved MCP namespace"
    ):
        soleaux.contracts.config.ResolvedConfig(mcp={namespace: _mcp_model()})


def test_mcp_prefix_ambiguous_namespaces_are_rejected() -> None:
    with _assertions.raises_with_message(
        pydantic.ValidationError, "prefix-ambiguous MCP namespaces"
    ):
        soleaux.contracts.config.ResolvedConfig(mcp={"foo": _mcp_model(), "foo_bar": _mcp_model()})


def test_mcp_cache_and_timeout_boundaries_are_accepted() -> None:
    lower = _mcp_model(
        cache_ttl_seconds=0,
        request_timeout_seconds=0.001,
        init_timeout_seconds=0.001,
    )
    upper = _mcp_model(
        cache_ttl_seconds=300,
        request_timeout_seconds=300,
        init_timeout_seconds=60,
    )

    assert (lower.cache_ttl_seconds, lower.request_timeout_seconds, lower.init_timeout_seconds) == (
        0,
        0.001,
        0.001,
    )
    assert (upper.cache_ttl_seconds, upper.request_timeout_seconds, upper.init_timeout_seconds) == (
        300,
        300,
        60,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("cache_ttl_seconds", -0.001, id="negative-cache-ttl"),
        pytest.param("cache_ttl_seconds", 300.001, id="cache-ttl-over-max"),
        pytest.param("cache_ttl_seconds", float("nan"), id="nan-cache-ttl"),
        pytest.param("cache_ttl_seconds", float("inf"), id="infinite-cache-ttl"),
        pytest.param("request_timeout_seconds", 0, id="zero-request-timeout"),
        pytest.param("request_timeout_seconds", 300.001, id="request-timeout-over-max"),
        pytest.param("request_timeout_seconds", float("nan"), id="nan-request-timeout"),
        pytest.param("request_timeout_seconds", float("inf"), id="infinite-request-timeout"),
        pytest.param("init_timeout_seconds", 0, id="zero-init-timeout"),
        pytest.param("init_timeout_seconds", 60.001, id="init-timeout-over-max"),
        pytest.param("init_timeout_seconds", float("nan"), id="nan-init-timeout"),
        pytest.param("init_timeout_seconds", float("inf"), id="infinite-init-timeout"),
    ],
)
def test_mcp_cache_and_timeout_invalid_values_are_rejected(field: str, value: float) -> None:
    with pytest.raises(pydantic.ValidationError):
        _mcp_model(**{field: value})


@pytest.mark.parametrize(
    ("url", "tls_verify"),
    [
        pytest.param("https://example.com/mcp", True, id="remote-https"),
        pytest.param("http://localhost/mcp", False, id="localhost-http"),
        pytest.param("http://127.0.0.2/mcp", False, id="ipv4-loopback-http"),
        pytest.param("http://[::1]/mcp", False, id="ipv6-loopback-http"),
    ],
)
def test_mcp_url_policy_accepts_https_and_loopback_http(url: str, tls_verify: bool) -> None:
    backend = soleaux.contracts.config.McpBackendConfig(url=url, tls_verify=tls_verify)

    assert backend.url == url
    assert backend.tls_verify is tls_verify


def test_shared_lifecycle_requires_an_explicitly_stateless_http_backend() -> None:
    with _assertions.raises_with_message(
        pydantic.ValidationError, "requires a stateless HTTP backend"
    ):
        soleaux.contracts.config.McpBackendConfig(command=["backend"], lifecycle="shared")
    with _assertions.raises_with_message(
        pydantic.ValidationError, "requires a stateless HTTP backend"
    ):
        soleaux.contracts.config.McpBackendConfig(url="https://example.com/mcp", lifecycle="shared")

    backend = soleaux.contracts.config.McpBackendConfig(
        url="https://example.com/mcp",
        lifecycle="shared",
        stateless=True,
    )
    assert backend.lifecycle == "shared"
    assert backend.stateless is True


def test_session_lifecycle_requires_a_command_backend() -> None:
    with _assertions.raises_with_message(pydantic.ValidationError, "requires a command backend"):
        soleaux.contracts.config.McpBackendConfig(
            url="https://example.com/mcp", lifecycle="session"
        )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        pytest.param(
            {"url": "http://example.com/mcp"},
            "require HTTPS",
            id="remote-http",
        ),
        pytest.param(
            {"url": "https://user:pass@example.com/mcp"},
            "userinfo or a fragment",
            id="userinfo",
        ),
        pytest.param(
            {"url": "https://example.com/mcp#fragment"},
            "userinfo or a fragment",
            id="fragment",
        ),
        pytest.param(
            {"url": "ftp://example.com/mcp"},
            "HTTP or HTTPS",
            id="wrong-scheme",
        ),
        pytest.param(
            {"url": "https:///mcp"},
            "HTTP or HTTPS",
            id="missing-host",
        ),
        pytest.param(
            {"url": "https://example.com/mcp", "tls_verify": False},
            "only for loopback",
            id="remote-tls-disabled",
        ),
        pytest.param(
            {
                "url": "http://localhost/mcp",
                "tls_verify": False,
                "tls_ca_file_env": "SSL_CERT_FILE",
            },
            "CA file requires verification",
            id="ca-with-verification-disabled",
        ),
    ],
)
def test_mcp_url_and_tls_policy_rejects_unsafe_values(
    payload: dict[str, object], message: str
) -> None:
    with _assertions.raises_with_message(pydantic.ValidationError, message):
        soleaux.contracts.config.McpBackendConfig.model_validate(payload)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        pytest.param(
            {"url": "https://example.com/mcp", "env": {"MODE": "test"}},
            "require a command backend",
            id="url-with-env",
        ),
        pytest.param(
            {"url": "https://example.com/mcp", "cwd": "subdir"},
            "require a command backend",
            id="url-with-cwd",
        ),
        pytest.param(
            {"command": ["backend"], "auth": "bearer_env", "auth_token_env": "TOKEN"},
            "require a URL backend",
            id="command-with-auth",
        ),
        pytest.param(
            {"command": ["backend"], "headers_from_env": {"X-Trace": "TRACE"}},
            "require a URL backend",
            id="command-with-headers",
        ),
        pytest.param(
            {"command": ["backend"], "tls_ca_file_env": "SSL_CERT_FILE"},
            "require a URL backend",
            id="command-with-ca",
        ),
        pytest.param(
            {"command": ["backend"], "tls_verify": False},
            "require a URL backend",
            id="command-with-tls-policy",
        ),
    ],
)
def test_mcp_source_specific_fields_are_rejected(payload: dict[str, object], message: str) -> None:
    with _assertions.raises_with_message(pydantic.ValidationError, message):
        soleaux.contracts.config.McpBackendConfig.model_validate(payload)


def test_mcp_external_auth_header_and_ca_references_are_accepted() -> None:
    backend = soleaux.contracts.config.McpBackendConfig(
        url="https://example.com/mcp",
        auth="bearer_env",
        auth_token_env="MCP_TOKEN",
        headers_from_env={"X-API-Key": "MCP_API_KEY", "X-Trace": "TRACE_ID"},
        tls_ca_file_env="SSL_CERT_FILE",
    )

    assert backend.auth_token_env == "MCP_TOKEN"
    assert backend.headers_from_env == {
        "X-API-Key": "MCP_API_KEY",
        "X-Trace": "TRACE_ID",
    }
    assert backend.tls_ca_file_env == "SSL_CERT_FILE"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        pytest.param(
            {"auth": "bearer_env", "auth_token_env": "INVALID-NAME"},
            "environment variable names",
            id="invalid-auth-env",
        ),
        pytest.param(
            {"tls_ca_file_env": "INVALID NAME"},
            "environment variable names",
            id="invalid-ca-env",
        ),
        pytest.param(
            {"headers_from_env": {"Bad Header": "HEADER_VALUE"}},
            "unique, safe HTTP tokens",
            id="invalid-header-name",
        ),
        pytest.param(
            {"headers_from_env": {"Authorization": "AUTH_HEADER"}},
            "unique, safe HTTP tokens",
            id="forbidden-header",
        ),
        pytest.param(
            {"headers_from_env": {"X-Trace": "INVALID-NAME"}},
            "reference environment variables",
            id="invalid-header-env",
        ),
        pytest.param(
            {"headers_from_env": {"X-Trace": "TRACE_A", "x-trace": "TRACE_B"}},
            "unique, safe HTTP tokens",
            id="case-insensitive-duplicate-header",
        ),
    ],
)
def test_mcp_external_reference_names_are_validated(
    payload: dict[str, object], message: str
) -> None:
    with _assertions.raises_with_message(pydantic.ValidationError, message):
        soleaux.contracts.config.McpBackendConfig.model_validate(
            {"url": "https://example.com/mcp", **payload}
        )


@pytest.mark.parametrize(
    "field",
    [
        "forward_incoming_headers",
        "forward_roots",
        "forward_sampling",
        "forward_elicitation",
        "forward_logs",
        "forward_progress",
    ],
)
def test_mcp_callback_forwarding_cannot_be_enabled(field: str) -> None:
    with pytest.raises(pydantic.ValidationError):
        _mcp_model(**{field: True})


def test_mcp_callback_and_failure_defaults_are_fail_open_and_deny_forwarding() -> None:
    backend = _mcp_model()

    assert backend.stateless is False
    assert backend.forward_incoming_headers is False
    assert backend.forward_roots is False
    assert backend.forward_sampling is False
    assert backend.forward_elicitation is False
    assert backend.forward_logs is False
    assert backend.forward_progress is False
    assert backend.fail_open is True


def test_mcp_legacy_bare_auth_token_env_infers_bearer_env() -> None:
    backend = soleaux.contracts.config.McpBackendConfig.model_validate(
        {"url": "https://example.com/mcp", "auth_token_env": "TOKEN"}
    )

    assert backend.auth == "bearer_env"
    assert backend.auth_token_env == "TOKEN"


def test_mcp_oauth_client_name_requires_oauth_auth() -> None:
    with _assertions.raises_with_message(
        pydantic.ValidationError, 'MCP OAuth fields require auth = "oauth"'
    ):
        soleaux.contracts.config.McpBackendConfig.model_validate(
            {"url": "https://example.com/mcp", "oauth_client_name": "Custom"}
        )


def test_mcp_fail_open_policy_cannot_be_disabled() -> None:
    with pytest.raises(pydantic.ValidationError):
        _mcp_model(fail_open=False)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        pytest.param(
            {"url": "https://example.com/mcp", "auth": "bearer_env"},
            "auth 'bearer_env' requires auth_token_env",
            id="bearer-without-token-env",
        ),
        pytest.param(
            {"url": "https://example.com/mcp", "auth": "none", "auth_token_env": "TOKEN"},
            'requires auth = "bearer_env"',
            id="explicit-none-with-token-env",
        ),
        pytest.param(
            {"command": ["backend"], "auth": "oauth"},
            "auth 'oauth' requires a URL backend",
            id="oauth-with-command",
        ),
        pytest.param(
            {
                "url": "https://example.com/mcp",
                "auth": "oauth",
                "auth_token_env": "TOKEN",
            },
            "mutually exclusive with auth_token_env",
            id="oauth-with-token-env",
        ),
    ],
)
def test_mcp_auth_modes_require_matching_fields(payload: dict[str, object], message: str) -> None:
    with _assertions.raises_with_message(pydantic.ValidationError, message):
        soleaux.contracts.config.McpBackendConfig.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"oauth_scopes": ["read"]}, id="scopes"),
        pytest.param(
            {"oauth_client_metadata_url": "https://example.com/client.json"},
            id="client-metadata-url",
        ),
        pytest.param({"client_id_env": "CLIENT_ID"}, id="client-id-env"),
        pytest.param(
            {"client_id_env": "CLIENT_ID", "client_secret_env": "CLIENT_SECRET"},
            id="client-secret-env",
        ),
        pytest.param({"token_store": "keyring"}, id="token-store"),
    ],
)
@pytest.mark.parametrize("auth", ["none", "bearer_env"])
def test_mcp_oauth_fields_require_oauth_auth(auth: str, payload: dict[str, object]) -> None:
    base: dict[str, object] = {"url": "https://example.com/mcp", "auth": auth}
    if auth == "bearer_env":
        base["auth_token_env"] = "TOKEN"
    with _assertions.raises_with_message(
        pydantic.ValidationError, 'MCP OAuth fields require auth = "oauth"'
    ):
        soleaux.contracts.config.McpBackendConfig.model_validate({**base, **payload})


@pytest.mark.parametrize(
    "metadata_url",
    [
        pytest.param("http://example.com/client.json", id="http-scheme"),
        pytest.param("https://example.com", id="empty-path"),
        pytest.param("https://example.com/", id="root-path"),
        pytest.param("https://user@example.com/client.json", id="userinfo"),
        pytest.param("https://user:pass@example.com/client.json", id="userinfo-password"),
        pytest.param("https://example.com/client.json#fragment", id="fragment"),
    ],
)
def test_mcp_oauth_client_metadata_url_requires_an_https_non_root_path(
    metadata_url: str,
) -> None:
    with _assertions.raises_with_message(
        pydantic.ValidationError, "HTTPS URL with a non-root path"
    ):
        soleaux.contracts.config.McpBackendConfig.model_validate(
            {
                "url": "https://example.com/mcp",
                "auth": "oauth",
                "oauth_client_metadata_url": metadata_url,
            }
        )


@pytest.mark.parametrize(
    "scopes",
    [
        pytest.param(["read write"], id="embedded-space"),
        pytest.param(["read\twrite"], id="embedded-tab"),
        pytest.param([""], id="empty-scope"),
    ],
)
def test_mcp_oauth_scopes_reject_empty_and_whitespace_elements(scopes: list[str]) -> None:
    with _assertions.raises_with_message(
        pydantic.ValidationError, "nonempty, whitespace-free elements"
    ):
        soleaux.contracts.config.McpBackendConfig.model_validate(
            {
                "url": "https://example.com/mcp",
                "auth": "oauth",
                "oauth_scopes": scopes,
            }
        )


def test_mcp_oauth_client_name_must_be_nonempty() -> None:
    with _assertions.raises_with_message(pydantic.ValidationError, "nonempty, NUL-free string"):
        soleaux.contracts.config.McpBackendConfig.model_validate(
            {
                "url": "https://example.com/mcp",
                "auth": "oauth",
                "oauth_client_name": "",
            }
        )


def test_mcp_oauth_client_secret_requires_a_client_id() -> None:
    with _assertions.raises_with_message(
        pydantic.ValidationError, "client_secret_env requires client_id_env"
    ):
        soleaux.contracts.config.McpBackendConfig.model_validate(
            {
                "url": "https://example.com/mcp",
                "auth": "oauth",
                "client_secret_env": "CLIENT_SECRET",
            }
        )


@pytest.mark.parametrize("field", ["client_id_env", "client_secret_env"])
def test_mcp_oauth_client_references_must_be_environment_names(field: str) -> None:
    payload: dict[str, object] = {
        "url": "https://example.com/mcp",
        "auth": "oauth",
        "client_id_env": "CLIENT_ID",
        "client_secret_env": "CLIENT_SECRET",
    }
    payload[field] = "INVALID-NAME"
    with _assertions.raises_with_message(pydantic.ValidationError, "environment variable names"):
        soleaux.contracts.config.McpBackendConfig.model_validate(payload)


def test_mcp_oauth_defaults_are_disk_store_and_empty_scopes() -> None:
    backend = soleaux.contracts.config.McpBackendConfig(url="https://example.com/mcp", auth="oauth")

    assert backend.oauth_scopes == ()
    assert backend.oauth_client_name == "Soleaux"
    assert backend.oauth_client_metadata_url is None
    assert backend.client_id_env is None
    assert backend.client_secret_env is None
    assert backend.token_store == "disk"


def test_mcp_oauth_config_preserves_explicit_values() -> None:
    backend = soleaux.contracts.config.McpBackendConfig.model_validate(
        {
            "url": "https://example.com/mcp",
            "auth": "oauth",
            "oauth_scopes": ["read", "write"],
            "oauth_client_name": "Soleaux Test",
            "oauth_client_metadata_url": "https://example.com/oauth/client.json",
            "client_id_env": "CLIENT_ID",
            "client_secret_env": "CLIENT_SECRET",
            "token_store": "keyring",
        }
    )

    assert backend.auth == "oauth"
    assert backend.oauth_scopes == ("read", "write")
    assert backend.oauth_client_name == "Soleaux Test"
    assert backend.oauth_client_metadata_url == "https://example.com/oauth/client.json"
    assert backend.client_id_env == "CLIENT_ID"
    assert backend.client_secret_env == "CLIENT_SECRET"
    assert backend.token_store == "keyring"


def test_config_digest_binds_exact_content() -> None:
    assert soleaux.contracts.config.config_digest(
        b"config"
    ) == soleaux.contracts.config.config_digest(b"config")
    assert soleaux.contracts.config.config_digest(
        b"config"
    ) != soleaux.contracts.config.config_digest(b"config ")
    assert len(soleaux.contracts.config.config_digest(b"")) == 64


def test_absent_policy_resolves_to_an_empty_typed_default() -> None:
    resolved = soleaux.contracts.config.ResolvedConfig.default()

    assert resolved.policy.backends == {}
    assert "policy" not in resolved.public_payload()


def test_policy_per_backend_default_and_tool_overrides_round_trip(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "soleaux.toml").write_text(
        "\n".join(
            (
                "[mcp.playwright]",
                'command = ["playwright", "run-mcp-server"]',
                "",
                "[policy.backends.playwright]",
                'default = "ask"',
                "",
                "[policy.backends.playwright.tools]",
                'browser_navigate = "allow"',
                'browser_run_code_unsafe = "deny"',
                "",
            )
        ),
        encoding="utf-8",
    )

    resolved = soleaux.contracts.config.load_config(tmp_path)

    backend = resolved.policy.backends["playwright"]
    assert backend.default is soleaux.contracts.config.PolicyEffect.ASK
    assert backend.tools == {
        "browser_navigate": soleaux.contracts.config.PolicyEffect.ALLOW,
        "browser_run_code_unsafe": soleaux.contracts.config.PolicyEffect.DENY,
    }
    assert resolved.public_payload()["policy"] == {
        "backends": {
            "playwright": {
                "default": "ask",
                "tools": {
                    "browser_navigate": "allow",
                    "browser_run_code_unsafe": "deny",
                },
            }
        }
    }


def test_policy_backend_defaults_to_ask_with_no_overrides() -> None:
    resolved = soleaux.contracts.config.ResolvedConfig(
        mcp={"playwright": _mcp_model()},
        policy=soleaux.contracts.config.PolicyConfig.model_validate(
            {"backends": {"playwright": {}}}
        ),
    )

    backend = resolved.policy.backends["playwright"]
    assert backend.default is soleaux.contracts.config.PolicyEffect.ASK
    assert backend.tools == {}


def test_policy_tool_names_validate_shape_not_membership() -> None:
    backend = soleaux.contracts.config.PolicyBackendConfig.model_validate(
        {"tools": {"definitely_not_a_live_backend_tool": "allow"}}
    )

    assert backend.tools == {
        "definitely_not_a_live_backend_tool": soleaux.contracts.config.PolicyEffect.ALLOW
    }


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        pytest.param({"default": "permit"}, "default", id="invalid-default-effect"),
        pytest.param(
            {"tools": {"read": "permit"}},
            "tools",
            id="invalid-tool-effect",
        ),
        pytest.param({"tools": {"": "allow"}}, "nonempty", id="empty-tool-name"),
        pytest.param({"tools": {"bad\x00name": "allow"}}, "NUL-free", id="nul-tool-name"),
        pytest.param({"tools": {"*": "deny"}}, "non-wildcard", id="wildcard-tool-name"),
        pytest.param({"tools": {"browser_*": "deny"}}, "non-wildcard", id="glob-tool-name"),
        pytest.param({"bogus": 1}, "bogus", id="unknown-backend-key"),
    ],
)
def test_policy_backend_rejects_invalid_values(payload: dict[str, object], message: str) -> None:
    with _assertions.raises_with_message(pydantic.ValidationError, message):
        soleaux.contracts.config.PolicyBackendConfig.model_validate(payload)


def test_policy_unknown_section_keys_fail_clearly(tmp_path: pathlib.Path) -> None:
    (tmp_path / "soleaux.toml").write_text("[policy]\nbogus = 1\n", encoding="utf-8")

    with _assertions.raises_with_message(soleaux.contracts.config.ConfigError, "bogus"):
        soleaux.contracts.config.load_config(tmp_path)


def test_policy_backends_must_reference_declared_mcp_backends() -> None:
    with _assertions.raises_with_message(pydantic.ValidationError, "undeclared MCP backend"):
        soleaux.contracts.config.ResolvedConfig(
            policy=soleaux.contracts.config.PolicyConfig.model_validate({"backends": {"ghost": {}}})
        )
