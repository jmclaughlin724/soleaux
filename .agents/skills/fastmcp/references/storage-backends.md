# Storage Backends

## Source and Version Contract

Use this reference for the complete workflow represented by the live [FastMCP storage backends guide](https://gofastmcp.com/servers/storage-backends), verified 2026-07-14. FastMCP storage uses the async `py-key-value-aio` contract. Inspect the installed extras and backend package before choosing an implementation; a backend named in current docs may not be installed or production-ready in the owning project. See [Version and source routing](version-and-source-routing.md) for the pinned baseline.

## What Actually Resolves Here

**Only 3 of the 9 backend families below import on this repository's pin.** The declared dependency is `py-key-value-aio[filetree,keyring,memory]`, so:

| Module | Status |
| --- | --- |
| `key_value.aio.stores.memory` | **available** |
| `key_value.aio.stores.filetree` | **available** |
| `key_value.aio.stores.keyring` | **available** |
| `key_value.aio.stores.postgresql` | `ImportError: PostgreSQLStore requires py-key-value-aio[postgresql]` |
| `key_value.aio.stores.redis` | `ImportError: RedisStore requires py-key-value-aio[redis]` |
| `key_value.aio.stores.valkey` | `ImportError: ValkeyStore requires py-key-value-aio[valkey]` |
| `key_value.aio.stores.dynamodb` | `ImportError: DynamoDBStore requires py-key-value-aio[dynamodb]` |
| `key_value.aio.stores.memcached` | `ImportError: MemcachedStore requires py-key-value-aio[memcached]` |
| `key_value.aio.stores.rocksdb` | `ImportError: RocksDBStore requires py-key-value-aio[rocksdb]` |
| `key_value.aio.stores.mongodb` | `ModuleNotFoundError: No module named 'bson'` |
| `key_value.aio.stores.elasticsearch` | `ModuleNotFoundError: No module named 'elastic_transport'` |

The unavailable backends are documented below because they remain correct guidance for a project that declares the extra. **They are not usable here without a manifest change** to the owning `pyproject.toml`. Do not write code against one and discover the `ImportError` at deploy time — check the import first.

The wrappers are all available (`FernetEncryptionWrapper`, `PrefixCollectionsWrapper`, `LimitSizeWrapper`, `StatisticsWrapper`), since they wrap the protocol rather than a driver.

Note also that `keyring` — available here — is not in the table below; it backs the platform keychain that OAuth development defaults use. It is not a server-side durable store.

## Separate Storage Concerns

Do not use one undifferentiated store contract for everything. Identify the owner, namespace, retention, consistency, confidentiality, and failure behavior for each use:

- response cache;
- server-side OAuth client registrations and upstream tokens;
- client-side OAuth tokens;
- MCP session state through `session_state_store`;
- background-task queue and task metadata;
- application/domain data.

**Task storage is no longer a core setting.** The pinned release's `Settings` model carries no `docket` block and no `client_task_poll_interval`; both were removed when the task engine left core. Task state is owned by the separate `fastmcp-tasks` distribution, which this repository does not install, so there is no task-storage knob to configure from here at all. Read [Background tasks](tasks.md) before enabling it, and do not reach for `ResponseCachingMiddleware.cache_storage` as a substitute — it is the response cache, not a queue.

A repository may own a custom task adapter only when it implements and tests the complete protocol, queue, worker, lease, redelivery, progress, cancellation, result, and authorization contract. An application database is not automatically a FastMCP key-value or task backend.

## Available Backend Families

| Backend | Installed here | Best fit | Benefits | Constraints |
| --- | --- | --- | --- | --- |
| `MemoryStore` | **yes** | Development, tests, one-process ephemeral servers | No setup, very fast | Lost on restart; not shared across processes |
| `FileTreeStore` | **yes** | One trusted host needing persistence | Survives restart, no external service, inspectable files | Not distributed; filesystem permissions and corruption matter |
| `PostgreSQLStore` | no — needs extra | Existing PostgreSQL/Supabase infrastructure | Shared durable JSONB storage, transactions, backups | Requires the PostgreSQL extra, owned DDL, pooling and connection-budget review |
| `RedisStore` / `ValkeyStore` | no — needs extra | Multiple replicas or workers | Shared, low latency, TTL support, scalable | External service, network/auth/TLS operations |
| DynamoDB | no — needs extra | AWS-native distributed use | Managed durability and scale | Service cost, consistency/latency/maturity review |
| MongoDB | no — needs extra | Existing MongoDB infrastructure | Shared document-backed store | Backend-specific operational and maturity constraints |
| Elasticsearch | no — needs extra | Existing search infrastructure | Distributed persistence | Heavy dependency for simple key/value use; review backend maturity |
| Memcached | no — needs extra | Distributed cache only | Simple volatile cache | No durable source of truth |
| RocksDB | no — needs extra | Single-host embedded high performance | Local persistence and speed | Process/host ownership and binding support |

The remaining families are supplied through `py-key-value-aio` backends and extras. Inspect that library's matching release documentation, maturity, and limitations before production use.

### In-Memory

`MemoryStore()` is the default for many FastMCP storage paths. Use it only when losing state at restart and isolating each process is acceptable. It is appropriate for tests when the test also proves that production selects a different store where durability is required.

### File Storage

`FileTreeStore` needs both key and collection sanitization strategies. Without them, URL-like OAuth client IDs and other special keys can become invalid or traversal-prone paths.

```python
from pathlib import Path
from key_value.aio.stores.filetree import (
    FileTreeStore,
    FileTreeV1CollectionSanitizationStrategy,
    FileTreeV1KeySanitizationStrategy,
)

directory = Path("/var/lib/service/cache")
store = FileTreeStore(
    data_directory=directory,
    key_sanitization_strategy=FileTreeV1KeySanitizationStrategy(directory),
    collection_sanitization_strategy=FileTreeV1CollectionSanitizationStrategy(directory),
)
```

The V1 strategies keep safe alphanumeric names readable and hash unsafe names. Choose the strategy before writing production data: changing it later changes key mapping and is a storage migration, not a harmless refactor.

Run the process with least-privilege directory ownership. Do not place plaintext OAuth tokens, signing keys, or unrelated application data in an unencrypted file store.

### PostgreSQL or Supabase

Install the matching extra, `py-key-value-aio[postgresql]`, before importing `PostgreSQLStore` — **it is not installed here** and importing it now raises `ImportError`. The backend uses asyncpg, one table, a composite `(collection, key)` primary key, JSONB values, nullable TTL timestamps, and a partial expiry index. Own that table declaratively and set `auto_create=False`; a runtime identity should not own schema creation.

```python
from key_value.aio.stores.postgresql import PostgreSQLStore

store = PostgreSQLStore(
    url=settings.postgres_url,
    table_name="fastmcp_key_value",
    auto_create=False,
)
```

The backend accepts an unqualified table name, so pin the connection `search_path` to one internal schema and grant a dedicated login only `USAGE` on that schema plus `SELECT`, `INSERT`, `UPDATE`, and `DELETE` on the table. Keep the schema outside Supabase's exposed Data API schemas. Encrypt OAuth values with `FernetEncryptionWrapper` even though PostgreSQL storage is durable and managed.

For a persistent Supabase-hosted process, use the Dashboard's direct connection when the network can reach it. On IPv4-only networks, use Supavisor session mode on port 5432. Do not use transaction mode on port 6543 with this backend: transaction pooling does not support prepared statements and the installed store does not expose asyncpg's statement-cache configuration. Percent-encode special password characters, require SSL remotely, set a bounded pool/connection budget, and monitor connection consumption by the custom database role.

PostgreSQL can back FastMCP session state, response caching, and encrypted OAuth key-value records. It is not a task backend or an HTTP event-store implementation; keep those concerns on their independently supported backends.

### Redis or Valkey

Install the matching extra, commonly `py-key-value-aio[redis]` (or `[valkey]` for `ValkeyStore`), before importing `RedisStore` — **neither is installed here**. Configure host, port, authentication, TLS, timeouts, pool limits, and database/namespace through the owning deployment secret/config surface.

```python
from key_value.aio.stores.redis import RedisStore

store = RedisStore(
    host=settings.redis_host,
    port=settings.redis_port,
    password=settings.redis_password,
)
```

Redis/Valkey is the normal choice for shared caches or state across replicas. Confirm eviction policy, persistence mode, failure behavior, TTLs, and rollout compatibility. A shared service without namespacing can create collisions between servers and environments.

## FastMCP Use Cases

### Server-Side OAuth Storage

OAuth proxy/provider flows persist dynamic client registrations and upstream tokens. FastMCP development defaults vary by platform:

- macOS and Windows can auto-manage a key through the system keyring and default OAuth storage to disk;
- Linux defaults are ephemeral keys and in-memory storage.

These automatic defaults are for development and local testing. For production, configure both:

1. an explicit stable JWT signing key;
2. persistent network-accessible storage wrapped with `FernetEncryptionWrapper` and an explicit encryption key.

```python
from cryptography.fernet import Fernet
from fastmcp.server.auth.providers.github import GitHubProvider
from key_value.aio.stores.redis import RedisStore
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

encrypted_oauth_store = FernetEncryptionWrapper(
    key_value=RedisStore(
        host=settings.redis_host,
        port=settings.redis_port,
        password=settings.redis_password,
    ),
    fernet=Fernet(settings.storage_encryption_key),
)

auth = GitHubProvider(
    client_id=settings.oauth_client_id,
    client_secret=settings.oauth_client_secret,
    base_url=settings.public_base_url,
    jwt_signing_key=settings.jwt_signing_key,
    client_storage=encrypted_oauth_store,
)
```

This uses `GitHubProvider` only as a concrete example; other installed OAuth providers have their own constructor contracts. Without the encryption wrapper, the custom store holds sensitive upstream tokens in plaintext. Keep signing and encryption keys in the deployment secret manager, rotate them through an owned migration, and verify old records remain readable or are deliberately invalidated.

### Response Caching

Pass an `AsyncKeyValue` implementation as `ResponseCachingMiddleware(cache_storage=...)`. For one-host persistence, use a correctly sanitized `FileTreeStore`. For shared replicas, use Redis/Valkey or another reviewed distributed backend.

When multiple logical servers share one backend, wrap it with `PrefixCollectionsWrapper` or use an equivalent owner-approved namespace:

```python
from key_value.aio.wrappers.prefix_collections import PrefixCollectionsWrapper

cache_store = PrefixCollectionsWrapper(
    key_value=shared_store,
    prefix="service-production-v2",
)
```

Namespaces should include the service/environment and, when necessary, a schema or behavior version. Do not use actor identity as an ad hoc substitute for authorization. Review cache key identity rules in [Middleware](middleware.md).

### Protocol-Level Response Cache (v4)

v4 adds a **second, protocol-level cache that is distinct from `ResponseCachingMiddleware`**. The middleware is a server-side FastMCP construct; this one is an MCP-protocol feature (SEP-2549) where the server emits a cache _hint_ and the client decides whether to honor it. Do not conflate them — they have separate storage, separate configuration, and separate failure modes.

**Server side.** `FastMCP(cache_ttl=..., cache_scope=...)` sets one uniform hint, implemented in `fastmcp/server/caching.py`. `cache_ttl` is in **seconds** and converted to the wire's milliseconds. `cache_scope` is `Literal["public", "private"]` and defaults to `"private"` when a TTL is set; `"public"` means a cached result may be shared **across authorization contexts**, so treat it as a disclosure decision and leave it private unless the result is genuinely actor-independent.

The hint is uniform by construction: one server-level value applies to `tools/list`, `prompts/list`, `resources/list`, `resources/templates/list`, `resources/read`, and `server/discover` alike. There is no per-component surface. Two guardrails are enforced by `build_cache_hints`, both raising `ValueError`: a non-positive `cache_ttl`, and a `cache_scope` set without a `cache_ttl` (a scope alone cannot enable caching, so it is rejected rather than silently ignored).

**Client side.** `Client(cache=...)` accepts `CacheConfig | bool | None`. `CacheConfig`, `CacheMode`, `ClientResponseCache`, and `InMemoryResponseCacheStore` come from **`mcp.client.caching`** — the SDK, not FastMCP. `CacheMode = Literal["use", "refresh", "bypass"]` selects per-call behavior: `use` reads the cache, `refresh` re-fetches and repopulates, `bypass` ignores it entirely. `CacheConfig(store=None, partition="", target_id=None, default_ttl_ms=0, clock=time.time, share_public=False)` — note `share_public` defaults to `False`, so a server's `"public"` scope is not honored as shared unless the client opts in.

**FastMCP's contribution** is the durable store. `fastmcp/client/caching.py` provides `KeyValueResponseCacheStore(storage: AsyncKeyValue | None = None, *, collection: str = "fastmcp_response_cache")`, which backs the SDK cache with any `py-key-value-aio` store, alongside `CacheEntry`, `CacheKey`, `CacheableResult`, `MemoryStore`, `CACHEABLE_RESULT_MODELS`, `MONOLITH_RESULTS`, and `DEFAULT_CACHE_COLLECTION = "fastmcp_response_cache"`. Pass a persistent store here to survive client restarts; the SDK's own `InMemoryResponseCacheStore` does not.

Both sides are required. **A hinted server is inert unless the client passes `cache=` and negotiates the modern `2026-07-28` protocol era** — honoring is modern-only and opt-in. A server that sets `cache_ttl` and sees no caching should check the negotiated era before changing the TTL. Give the collection an explicit per-service name when several clients share one backend, for the same reasons namespacing matters for the middleware cache.

### Client-Side OAuth Tokens

FastMCP `OAuth` client auth uses memory by default. To reconnect without re-authenticating after a client restart, pass a persistent store as `token_storage`.

```python
from fastmcp.client.auth import OAuth

oauth = OAuth(
    mcp_url=settings.mcp_url,
    token_storage=client_token_store,
)
```

Protect the local directory or remote store as credential material. Use per-user ownership, encryption where required, explicit logout/revocation deletion, and no cross-user namespace sharing.

### MCP Session State

Pass `session_state_store=store` to `FastMCP` when `Context.get_state` / `set_state` must survive across requests or replicas. Each server instance otherwise owns its own default store. Mounted parent and child servers share serializable session state only when configured with the same store.

Session keys are automatically scoped by the MCP session in installed source, but callers must still namespace application-level keys when domains, tenants, or feature versions could collide. Serializable state receives the installed retention TTL; confirm that TTL and backend enforcement before relying on it. `serializable=False` remains request-local and never enters this backend.

## Selection Workflow

1. If state may be lost at restart and only one process uses it, choose memory.
2. If one trusted host needs restart persistence, choose a sanitized file store.
3. If processes or replicas must share durable state and PostgreSQL is already operated, review and choose `PostgreSQLStore`; use Redis/Valkey when its latency, task, or replay support is required.
4. If the organization already owns a supported store, verify the matching `py-key-value-aio` backend's maturity before reusing it.
5. For OAuth secrets, add encryption at rest regardless of backend persistence.
6. For caches, define TTL, invalidation, size, namespace, and actor isolation.
7. For durable state, define consistency, migration, backup/restore, deletion, and fail-open/fail-closed behavior.

## Verification

Test through the FastMCP feature that consumes the store rather than only calling the backend directly. Cover:

- the chosen backend actually imports in the environment that will run it, before any code depends on it;
- restart persistence or deliberate loss, matching the selected contract;
- two-process or two-replica visibility where sharing is required;
- TTL expiry, namespace isolation, invalidation, and maximum item behavior;
- unavailable backend, timeout, reconnect, partial write, and corrupt record paths;
- file permissions, sanitization of URL/special-character keys, and migration if strategies change;
- Redis/Valkey authentication, TLS, eviction, and deployment failover;
- PostgreSQL schema ownership, exact table contract, least-privilege grants, SSL, pool mode, connection budget, expiry cleanup, and backup/restore;
- OAuth records are encrypted at rest and decrypt after restart/rotation as designed;
- client logout/revocation deletes stored tokens;
- session isolation, mounted-server sharing, and replica routing;
- for the protocol-level cache, a hinted server against a client with and without `cache=`, on both a modern and a legacy negotiated era, plus `use`/`refresh`/`bypass` modes and `share_public` behavior;
- no raw credentials or protected values appear in logs, metrics, exceptions, or cache keys.
