//! Unified model-facing MCP server for `soleaux serve`.
//!
//! Phase 1 exposes exactly the twelve canonical public tools from the binding
//! profile, or an explicitly substituted twelve-slot profile. User/team MCPs,
//! skills, agents, rules, and control-plane operations remain behind registry,
//! gateway, resources, or CLI surfaces and never inflate `tools/list`.

pub mod editor;
pub mod envelope;
pub mod gateway;
pub mod http;
pub mod memory;
pub mod profile;
pub mod provisioning;
pub mod registry;
mod schema;
pub mod semantic;

use anyhow::{Context, Result, bail};
use editor::{EditorService, StoredPreview};
use envelope::{
    EvidenceRange, SuccessMetadata, ToolEnvelopeV2, ToolError, coverage, evidence, gap, provenance,
};
use memory::search_memory;
use registry::{RegistrySnapshot, detect_frameworks, scan_registry};
use semantic::SemanticService;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use soleaux_intelligence::{
    analyze_postgres_sql,
    context_v2::{
        CompileRequestV2, ContextPacketV2, ContextReference, RequestedResource, compile_v2,
    },
    governance::build_governance_graph,
    index::{IndexConfig, IndexReport, RepositoryIndex},
    lsp::{LspProbe, LspSupervisor, discover_workspace_servers},
    nextjs::index_nextjs,
    turborepo::{load_graph, packages_for_path, search_scope},
};
use soleaux_storage::{IndexedFileRecord, Store, SymbolHit, SymbolRecord};
use std::{
    collections::{BTreeMap, BTreeSet, HashMap},
    fs,
    path::{Path, PathBuf},
    sync::{Arc, OnceLock, RwLock},
    time::Instant,
};
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    sync::{Mutex as AsyncMutex, RwLock as AsyncRwLock},
};
use uuid::Uuid;

pub const MCP_STABLE_VERSION: &str = "2025-11-25";
pub const MCP_EXPERIMENTAL_VERSION: &str = "2026-07-28";
pub const PUBLIC_ROOT_TOOL_COUNT: usize = profile::HARD_CEILING;
pub const PUBLIC_ROOT_TOOL_MAX: usize = profile::HARD_CEILING;
pub const MAX_RESULT_BYTES: usize = 256 * 1024;

pub const OPTIONAL_POSTGRES: &str = "parse_and_validate_postgres_sql";
pub const OPTIONAL_TURBOREPO: &str = "turborepo.packages";
pub const OPTIONAL_NEXTJS: &str = "next.get_routes";

type SearchMatchesResult = (Vec<Value>, Vec<String>, Vec<Value>, bool);
type SymbolsDataResult = (Value, Vec<Value>, bool, Vec<Value>, Vec<String>, bool);
type ResolvedResourceResult = (String, Option<String>, Option<String>, Option<String>);

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ToolDefinition {
    pub name: String,
    pub description: String,
    pub input_schema: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub struct ToolSubstitution {
    pub replace: String,
    pub with: String,
}

#[derive(Clone)]
pub struct PublicMcpServer {
    root: Arc<PathBuf>,
    index: RepositoryIndex,
    active_tools: Arc<Vec<String>>,
    substitutions: Arc<Vec<ToolSubstitution>>,
    last_index_report: Arc<RwLock<Option<IndexReport>>>,
    lsp: LspSupervisor,
    language_servers: Arc<AsyncRwLock<HashMap<String, String>>>,
    lsp_probes: Arc<AsyncRwLock<Vec<LspProbe>>>,
    semantic: SemanticService,
    registry: Arc<RwLock<RegistrySnapshot>>,
    editor: EditorService,
    repository_read_refresh: Arc<AsyncMutex<()>>,
}

impl PublicMcpServer {
    pub fn new(root: impl AsRef<Path>) -> Result<Self> {
        let root = fs::canonicalize(root.as_ref())
            .with_context(|| format!("resolving workspace {}", root.as_ref().display()))?;
        let home = std::env::var_os("SOLEAUX_HOME")
            .map(PathBuf::from)
            .or_else(|| dirs::home_dir().map(|path| path.join(".soleaux")))
            .context("unable to determine SOLEAUX_HOME")?;
        let workspace_hash = blake3::hash(root.to_string_lossy().as_bytes())
            .to_hex()
            .to_string();
        let store_path = home
            .join("indexes")
            .join(format!("{}.sqlite3", &workspace_hash[..32]));
        Self::with_store(root, store_path)
    }

    pub fn with_store(root: impl AsRef<Path>, store_path: impl AsRef<Path>) -> Result<Self> {
        let root = fs::canonicalize(root.as_ref())
            .with_context(|| format!("resolving workspace {}", root.as_ref().display()))?;
        if !root.is_dir() {
            bail!("workspace root is not a directory");
        }
        let store = Store::open(store_path)?;
        let index = RepositoryIndex::open(&root, store, IndexConfig::default())?;
        let lsp = LspSupervisor::new(64 * 1024 * 1024);
        let language_servers = Arc::new(AsyncRwLock::new(HashMap::new()));
        let lsp_probes = Arc::new(AsyncRwLock::new(Vec::new()));
        let semantic = SemanticService::new(
            index.clone(),
            lsp.clone(),
            Arc::clone(&language_servers),
            Arc::clone(&lsp_probes),
        );
        let registry = scan_registry(&root, &index)?;
        let editor = EditorService::new(index.clone())?;
        Ok(Self {
            root: Arc::new(root),
            index,
            active_tools: Arc::new(
                profile::CANONICAL_TOOL_NAMES
                    .iter()
                    .map(|name| (*name).to_string())
                    .collect(),
            ),
            substitutions: Arc::new(Vec::new()),
            last_index_report: Arc::new(RwLock::new(None)),
            lsp,
            language_servers,
            lsp_probes,
            semantic,
            registry: Arc::new(RwLock::new(registry)),
            editor,
            repository_read_refresh: Arc::new(AsyncMutex::new(())),
        })
    }

    pub fn substitute_tool(mut self, replace: &str, with: &str) -> Result<Self> {
        if !profile::CANONICAL_TOOL_NAMES.contains(&replace) {
            bail!("substitution replace value is not a canonical tool: {replace}");
        }
        if !profile::OPTIONAL_TOOL_NAMES.contains(&with) {
            bail!("substitution target is not an optional candidate: {with}");
        }
        if !self.optional_provider_available(with)? {
            bail!("optional provider is disabled or unavailable: {with}");
        }
        if self
            .substitutions
            .iter()
            .any(|entry| entry.replace == replace)
        {
            bail!("canonical slot is already substituted: {replace}");
        }
        if self.substitutions.iter().any(|entry| entry.with == with) {
            bail!("optional candidate is already active: {with}");
        }
        let mut active = (*self.active_tools).clone();
        let position = active
            .iter()
            .position(|name| name == replace)
            .with_context(|| format!("canonical slot is not currently active: {replace}"))?;
        if active.iter().any(|name| name == with) {
            bail!("optional candidate would create a duplicate active tool: {with}");
        }
        active[position] = with.to_string();
        validate_active_profile(&active)?;
        let mut substitutions = (*self.substitutions).clone();
        substitutions.push(ToolSubstitution {
            replace: replace.to_string(),
            with: with.to_string(),
        });
        self.active_tools = Arc::new(active);
        self.substitutions = Arc::new(substitutions);
        Ok(self)
    }

    async fn refresh_repository_read_state(&self) -> Result<IndexReport> {
        let _guard = self.repository_read_refresh.lock().await;
        let report = self.index.refresh().await?;
        *self
            .last_index_report
            .write()
            .expect("index report lock poisoned") = Some(report.clone());
        *self.registry.write().expect("registry lock poisoned") =
            scan_registry(self.root(), &self.index)?;
        Ok(report)
    }

    pub async fn prepare(&self) -> Result<IndexReport> {
        validate_active_profile(&self.active_tools)?;
        let report = self.refresh_repository_read_state().await?;
        let languages = self.index.languages()?;
        let specs = discover_workspace_servers(self.root(), &languages)?;
        let mut routes = HashMap::new();
        let mut probes = Vec::new();
        for spec in specs {
            let route = spec.server_id.clone();
            match self.lsp.ensure_server(spec).await {
                Ok(probe) => {
                    routes.insert(route, probe.server_id.clone());
                    probes.push(probe);
                }
                Err(error) => tracing::warn!(
                    server = %route,
                    error = %error,
                    "native LSP capability probe failed; semantic tools remain honest about unavailable coverage"
                ),
            }
        }
        *self.language_servers.write().await = routes;
        *self.lsp_probes.write().await = probes;
        Ok(report)
    }

    pub fn workspace_id(&self) -> Uuid {
        self.index.workspace_id()
    }

    pub fn root(&self) -> &Path {
        self.root.as_ref()
    }

    pub fn substitutions(&self) -> &[ToolSubstitution] {
        self.substitutions.as_ref()
    }

    pub fn active_tool_names(&self) -> &[String] {
        self.active_tools.as_ref()
    }

    pub fn tools(&self) -> Vec<ToolDefinition> {
        let definitions = all_tool_definitions();
        let tools = self
            .active_tools
            .iter()
            .map(|name| {
                definitions
                    .get(name)
                    .unwrap_or_else(|| panic!("binding profile omitted active tool {name}"))
                    .clone()
            })
            .collect::<Vec<_>>();
        debug_assert_eq!(tools.len(), PUBLIC_ROOT_TOOL_COUNT);
        tools
    }

    fn is_public_tool(&self, name: &str) -> bool {
        self.active_tools.iter().any(|active| active == name)
    }

    fn validate_tool_arguments(&self, name: &str, arguments: &Value) -> Result<()> {
        if !self.is_public_tool(name) {
            bail!("tool is not active in the binding Soleaux public profile: {name}");
        }
        let definitions = all_tool_definitions();
        let definition = definitions
            .get(name)
            .with_context(|| format!("binding profile omitted active tool definition: {name}"))?;
        schema::validate_json_schema(&definition.input_schema, arguments)
            .with_context(|| format!("invalid arguments for {name}"))
    }

    pub async fn call_async(&self, name: &str, arguments: &Value) -> Result<ToolEnvelopeV2> {
        self.validate_tool_arguments(name, arguments)?;
        if requires_fresh_repository_state(name) {
            self.refresh_repository_read_state().await?;
        }
        let started = Instant::now();
        match name {
            "context.compile" => self.call_context(arguments, started).await,
            "code.search" => self.call_search(arguments, started).await,
            "memory.search" => self.call_memory(arguments, started),
            "get_symbols" => self.call_symbols(arguments, started),
            "registry.list" => self.call_registry_list(arguments, started),
            "registry.read" => self.call_registry_read(arguments, started),
            "repo_info" => self.call_repo_info(started).await,
            "navigate" => self.call_navigate(arguments, started).await,
            "inspect" => self.call_inspect(arguments, started).await,
            "preview" => self.call_preview(arguments, started).await,
            "edit" => self.call_edit(arguments, started).await,
            "restart_lsp" => self.call_restart_lsp(arguments, started).await,
            OPTIONAL_POSTGRES => self.call_postgres(arguments, started),
            OPTIONAL_TURBOREPO => self.call_turborepo(arguments, started),
            OPTIONAL_NEXTJS => self.call_nextjs(started),
            _ => bail!("tool is not active in the binding Soleaux public profile: {name}"),
        }
    }

    async fn call_context(&self, arguments: &Value, started: Instant) -> Result<ToolEnvelopeV2> {
        let objective = required_string(arguments, "objective")?.to_string();
        let references = arguments
            .get("references")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .cloned()
            .map(serde_json::from_value::<ContextReference>)
            .collect::<serde_json::Result<Vec<_>>>()?;
        let resource_uris = string_array(arguments, "resource_uris", 32)?;
        let requested_resources = resource_uris
            .iter()
            .map(|uri| self.resolve_context_resource(uri))
            .collect::<Result<Vec<_>>>()?;
        let semantic_mode = arguments
            .get("semantic_mode")
            .and_then(Value::as_str)
            .unwrap_or("best_available")
            .to_string();
        let request = CompileRequestV2 {
            objective,
            paths: string_array(arguments, "paths", 256)?,
            terms: string_array(arguments, "terms", 256)?,
            references,
            requested_resources,
            byte_budget: arguments
                .get("max_bytes")
                .and_then(Value::as_u64)
                .unwrap_or(32_768)
                .clamp(1, 262_144) as usize,
            token_budget: arguments
                .get("token_budget")
                .and_then(Value::as_u64)
                .unwrap_or(8_000)
                .clamp(256, 64_000) as usize,
            limit: arguments
                .get("limit")
                .and_then(Value::as_u64)
                .unwrap_or(50)
                .clamp(1, 200) as usize,
            relation_depth: arguments
                .get("relation_depth")
                .and_then(Value::as_u64)
                .unwrap_or(2)
                .clamp(0, 3) as u8,
            semantic_mode,
            semantic_available: !self.lsp_probes.read().await.is_empty(),
        };
        let packet = compile_v2(&self.index, &request)?;
        let data = serde_json::to_value(&packet)?;
        let mut metadata =
            SuccessMetadata::repository("context.compile", "soleaux-native-context-v2");
        metadata.trust = "verified_compiled_context".to_string();
        metadata.cache_status = packet.native.cache_status.clone();
        metadata.snapshot_id = packet.snapshot_id.clone();
        metadata.coverage = Some(serde_json::to_value(&packet.coverage)?);
        metadata.evidence = context_evidence(&packet);
        metadata.truncated = packet.response_truncated;
        metadata.continuation_cursor = packet.truncation.continuation_cursor.clone();
        metadata.sensitivity = if packet.secret_redactions > 0 {
            "secret_redacted".to_string()
        } else {
            "internal".to_string()
        };
        metadata.suggested_next_requests = context_suggestions(&packet);
        Ok(ToolEnvelopeV2::success(
            self.workspace_id(),
            &self.root.to_string_lossy(),
            data,
            None,
            elapsed_us(started),
            metadata,
        ))
    }

    async fn call_search(&self, arguments: &Value, started: Instant) -> Result<ToolEnvelopeV2> {
        let query = required_string(arguments, "query")?;
        let limit = arguments
            .get("limit")
            .and_then(Value::as_u64)
            .unwrap_or(20)
            .clamp(1, 200) as usize;
        let paths = string_array(arguments, "paths", 256)?;
        let kinds = string_array(arguments, "kinds", 32)?;
        let semantic_mode = arguments
            .get("semantic_mode")
            .and_then(Value::as_str)
            .unwrap_or("best_available");
        let (matches, observed_paths, mut gaps, truncated) =
            self.search_matches(query, &paths, &kinds, limit)?;
        if semantic_mode == "semantic_required" && self.lsp_probes.read().await.is_empty() {
            gaps.push(gap(
                "semantic_provider_unavailable",
                "semantic_required was requested but no native LSP completed its capability probe.",
                "error",
                true,
                Some("repository.semantic"),
                None,
            ));
        }
        let complete = gaps.is_empty() && !truncated;
        let data = json!({
            "query": query,
            "matches": matches,
            "coverage_complete": complete,
            "gaps": gaps,
        });
        let mut metadata =
            SuccessMetadata::repository("code.search", "soleaux-native-hybrid-search");
        metadata.trust = "verified_code_structure".to_string();
        metadata.cache_status = "read".to_string();
        metadata.coverage = Some(coverage(
            complete,
            paths,
            observed_paths,
            Vec::new(),
            vec!["sqlite-fts5".to_string(), "bounded-text-scan".to_string()],
            data.get("gaps")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default(),
            None,
        ));
        let empty_matches = Vec::new();
        metadata.evidence = search_evidence(
            data.get("matches")
                .and_then(Value::as_array)
                .unwrap_or(&empty_matches),
        );
        metadata.truncated = truncated;
        metadata.continuation_cursor = truncated.then(|| format!("search:{}", limit));
        Ok(ToolEnvelopeV2::success(
            self.workspace_id(),
            &self.root.to_string_lossy(),
            data,
            None,
            elapsed_us(started),
            metadata,
        ))
    }

    fn call_memory(&self, arguments: &Value, started: Instant) -> Result<ToolEnvelopeV2> {
        let query = required_string(arguments, "query")?;
        let scopes = string_array(arguments, "scopes", 3)?;
        let limit = arguments
            .get("limit")
            .and_then(Value::as_u64)
            .unwrap_or(20)
            .clamp(1, 200) as usize;
        let data = search_memory(self.root(), self.workspace_id(), query, &scopes, limit)?;
        let attached = data
            .get("attached")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        let complete = data
            .get("coverage_complete")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        let gaps = data
            .get("gaps")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let mut metadata = SuccessMetadata::repository("memory.search", "soleaux-native-memory");
        metadata.trust = if attached {
            "retrieved_code_data"
        } else {
            "unavailable"
        }
        .to_string();
        metadata.cache_status = if attached { "read" } else { "not_attached" }.to_string();
        metadata.coverage = Some(coverage(
            complete,
            Vec::new(),
            Vec::new(),
            Vec::new(),
            vec!["soleaux-native-memory".to_string()],
            gaps,
            None,
        ));
        Ok(ToolEnvelopeV2::success(
            self.workspace_id(),
            &self.root.to_string_lossy(),
            data,
            None,
            elapsed_us(started),
            metadata,
        ))
    }

    fn call_symbols(&self, arguments: &Value, started: Instant) -> Result<ToolEnvelopeV2> {
        let (data, evidence_values, complete, gaps, observed_paths, truncated) =
            self.symbols_data(arguments)?;
        let mut metadata =
            SuccessMetadata::repository("get_symbols", "soleaux-native-structural-index");
        metadata.trust = "verified_code_structure".to_string();
        metadata.cache_status = "read".to_string();
        metadata.evidence = evidence_values;
        metadata.coverage = Some(coverage(
            complete,
            requested_symbol_paths(arguments)?,
            observed_paths,
            Vec::new(),
            vec!["oxc".to_string(), "tree-sitter".to_string()],
            gaps,
            None,
        ));
        metadata.truncated = truncated;
        metadata.continuation_cursor = truncated.then(|| "symbols:next".to_string());
        Ok(ToolEnvelopeV2::success(
            self.workspace_id(),
            &self.root.to_string_lossy(),
            data,
            None,
            elapsed_us(started),
            metadata,
        ))
    }

    fn call_registry_list(&self, arguments: &Value, started: Instant) -> Result<ToolEnvelopeV2> {
        let domain = arguments.get("domain").and_then(Value::as_str);
        let limit = arguments
            .get("limit")
            .and_then(Value::as_u64)
            .unwrap_or(100)
            .clamp(1, 200) as usize;
        let offset = cursor_offset(arguments.get("cursor").and_then(Value::as_str))?;
        let data = self
            .registry
            .read()
            .expect("registry lock poisoned")
            .list(domain, limit, offset);
        let entries = data
            .get("entries")
            .and_then(Value::as_array)
            .map_or(0, Vec::len);
        let mut metadata = SuccessMetadata::repository("registry.list", "soleaux-native-registry");
        metadata.cache_status = "read".to_string();
        metadata.next_cursor = (entries == limit).then(|| format!("offset:{}", offset + limit));
        Ok(ToolEnvelopeV2::success(
            self.workspace_id(),
            &self.root.to_string_lossy(),
            data,
            None,
            elapsed_us(started),
            metadata,
        ))
    }

    fn call_registry_read(&self, arguments: &Value, started: Instant) -> Result<ToolEnvelopeV2> {
        let (data, rows, warnings) = self
            .registry
            .read()
            .expect("registry lock poisoned")
            .read(&self.index, arguments)?;
        let complete = data
            .get("coverage_complete")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        let gaps = data
            .get("gaps")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let mut metadata = SuccessMetadata::repository("registry.read", "soleaux-native-registry");
        metadata.cache_status = "read".to_string();
        metadata.warnings = warnings;
        metadata.coverage = Some(coverage(
            complete,
            Vec::new(),
            Vec::new(),
            Vec::new(),
            vec!["sqlite-wal".to_string(), "repository-catalog".to_string()],
            gaps,
            None,
        ));
        Ok(ToolEnvelopeV2::success(
            self.workspace_id(),
            &self.root.to_string_lossy(),
            data,
            Some(rows),
            elapsed_us(started),
            metadata,
        ))
    }

    async fn call_repo_info(&self, started: Instant) -> Result<ToolEnvelopeV2> {
        let stats = self.index.store_stats()?;
        let registry = self
            .registry
            .read()
            .expect("registry lock poisoned")
            .clone();
        let probes = self.semantic.probes().await;
        let languages = self.index.languages()?;
        let gateway = crate::gateway::backend_status(self.root())?;
        let governance = build_governance_graph(self.root(), &[])?;
        let data = json!({
            "product": "Soleaux",
            "version": profile::PRODUCT_VERSION,
            "production_claim_allowed": profile::PRODUCTION_CLAIM_ALLOWED,
            "workspace_id": self.workspace_id().to_string(),
            "root": self.root.to_string_lossy(),
            "shape": {
                "file_count": stats.file_count,
                "symbol_count": stats.symbol_count,
                "languages": languages,
                "substitutions": self.substitutions.as_ref(),
            },
            "frameworks": detect_frameworks(self.root()),
            "storage": {
                "mode": "sqlite-wal-serialized-writer",
                "database_bytes": stats.database_bytes,
                "schema_version": stats.schema_version,
                "integrity": "opened_and_queryable",
            },
            "transport": ["stdio", "streamable-http"],
            "active_tools": self.active_tools.as_ref(),
            "hard_ceiling": profile::HARD_CEILING,
            "catalog_digest": registry.catalog_digest,
            "gateway": {
                "backends": gateway,
                "root_tool_inflation": false,
                "oauth_login": "cli-mediated-only",
            },
            "governance": {
                "edge_count": governance.edges.len(),
                "coverage_complete": governance.coverage_complete,
                "digest": governance.digest,
                "gaps": governance.gaps,
            },
            "native_selections": {
                "parsers": ["oxc", "tree-sitter-typescript", "tree-sitter-python", "tree-sitter-bash", "pg_query/libpg_query"],
                "language_servers": probes,
                "selected_parsers_native": true,
                "selected_lsps_native": true,
            },
        });
        let mut metadata =
            SuccessMetadata::repository("repo_info", "soleaux-native-repository-metadata");
        metadata.cache_status = "read".to_string();
        metadata.coverage = Some(coverage(
            true,
            Vec::new(),
            Vec::new(),
            Vec::new(),
            vec![
                "sqlite-wal".to_string(),
                "native-capability-probes".to_string(),
            ],
            Vec::new(),
            None,
        ));
        Ok(ToolEnvelopeV2::success(
            self.workspace_id(),
            &self.root.to_string_lossy(),
            data,
            None,
            elapsed_us(started),
            metadata,
        ))
    }

    async fn call_navigate(&self, arguments: &Value, started: Instant) -> Result<ToolEnvelopeV2> {
        let response = self.semantic.navigate(arguments).await?;
        let mut metadata = SuccessMetadata::repository("navigate", "soleaux-native-lsp-broker");
        metadata.trust = if response.cache_status == "not_attached" {
            "unavailable"
        } else {
            "verified_semantic_result"
        }
        .to_string();
        metadata.cache_status = response.cache_status;
        metadata.coverage = Some(response.coverage);
        metadata.warnings = response.warnings;
        Ok(ToolEnvelopeV2::success(
            self.workspace_id(),
            &self.root.to_string_lossy(),
            response.data,
            None,
            elapsed_us(started),
            metadata,
        ))
    }

    async fn call_inspect(&self, arguments: &Value, started: Instant) -> Result<ToolEnvelopeV2> {
        let response = self.semantic.inspect(arguments).await?;
        let mut metadata = SuccessMetadata::repository("inspect", "soleaux-native-lsp-broker");
        metadata.trust = if response.cache_status == "not_attached" {
            "unavailable"
        } else {
            "verified_semantic_result"
        }
        .to_string();
        metadata.cache_status = response.cache_status;
        metadata.coverage = Some(response.coverage);
        metadata.warnings = response.warnings;
        Ok(ToolEnvelopeV2::success(
            self.workspace_id(),
            &self.root.to_string_lossy(),
            response.data,
            None,
            elapsed_us(started),
            metadata,
        ))
    }

    async fn call_preview(&self, arguments: &Value, started: Instant) -> Result<ToolEnvelopeV2> {
        let operation = required_string(arguments, "operation")?;
        let preview = if operation == "structural_rewrite" {
            self.editor.structural_preview(arguments)?
        } else {
            let (_server_id, workspace_edit) =
                self.semantic.preview_workspace_edit(arguments).await?;
            self.editor.preview_from_workspace_edit(
                arguments,
                operation,
                &workspace_edit,
                vec![
                    "Revalidate every whole-file SHA-256 preimage".to_string(),
                    "Apply one confirmed preview atomically".to_string(),
                    "Refresh the native structural index".to_string(),
                    "Append a hash-chained audit event".to_string(),
                ],
            )?
        };
        let data = preview_data(&preview);
        let mut metadata =
            SuccessMetadata::repository("preview", "soleaux-native-hash-bound-editor");
        metadata.trust = "verified_code_structure".to_string();
        metadata.cache_status = "live".to_string();
        metadata.evidence = preview
            .patches
            .iter()
            .enumerate()
            .map(|(index, patch)| {
                evidence(
                    format!("preview:{}:{index}", preview.preview_id),
                    "edit_patch",
                    format!("Hash-bound patch for {}", patch.path),
                    "verified_code_structure",
                    provenance(
                        "soleaux-native-hash-bound-editor",
                        "source-range-patch",
                        Some(self.workspace_id()),
                        None,
                        Some(&patch.path),
                        Some(&patch.preimage_sha256),
                        "utf8-bytes-zero-based",
                    ),
                    EvidenceRange {
                        path: Some(&patch.path),
                        start_byte: Some(patch.start_byte as u64),
                        end_byte: Some(patch.end_byte as u64),
                        ..EvidenceRange::default()
                    },
                )
            })
            .collect();
        Ok(ToolEnvelopeV2::success(
            self.workspace_id(),
            &self.root.to_string_lossy(),
            data,
            None,
            elapsed_us(started),
            metadata,
        ))
    }

    async fn call_edit(&self, arguments: &Value, started: Instant) -> Result<ToolEnvelopeV2> {
        let preview_id = required_string(arguments, "preview_id")?;
        let digest = required_string(arguments, "digest")?;
        let confirm = arguments
            .get("confirm")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        let data = self.editor.apply(preview_id, digest, confirm).await?;
        *self.registry.write().expect("registry lock poisoned") =
            scan_registry(self.root(), &self.index)?;
        let mut metadata = SuccessMetadata::repository("edit", "soleaux-native-hash-bound-editor");
        metadata.trust = "verified_repository_metadata".to_string();
        metadata.cache_status = "live".to_string();
        Ok(ToolEnvelopeV2::success(
            self.workspace_id(),
            &self.root.to_string_lossy(),
            data,
            None,
            elapsed_us(started),
            metadata,
        ))
    }

    async fn call_restart_lsp(
        &self,
        arguments: &Value,
        started: Instant,
    ) -> Result<ToolEnvelopeV2> {
        let data = self.semantic.restart(arguments).await?;
        let mut metadata =
            SuccessMetadata::repository("restart_lsp", "soleaux-native-lsp-supervisor");
        metadata.trust = "verified_repository_metadata".to_string();
        metadata.cache_status = "live".to_string();
        Ok(ToolEnvelopeV2::success(
            self.workspace_id(),
            &self.root.to_string_lossy(),
            data,
            None,
            elapsed_us(started),
            metadata,
        ))
    }

    fn call_postgres(&self, arguments: &Value, started: Instant) -> Result<ToolEnvelopeV2> {
        let sql = required_string(arguments, "sql")?;
        let analysis = analyze_postgres_sql(sql)?;
        let data = json!({
            "valid": analysis.valid,
            "normalized": analysis.normalized,
            "fingerprint": analysis.fingerprint,
            "relations": analysis.relations,
            "statement_count": analysis.statement_count,
            "errors": [],
            "engine": analysis.engine,
            "engine_version": analysis.engine_version,
        });
        let mut metadata = SuccessMetadata::repository(OPTIONAL_POSTGRES, "pg_query/libpg_query");
        metadata.trust = "verified_sql_structure".to_string();
        metadata.cache_status = "live".to_string();
        Ok(ToolEnvelopeV2::success(
            self.workspace_id(),
            &self.root.to_string_lossy(),
            data,
            None,
            elapsed_us(started),
            metadata,
        ))
    }

    fn call_turborepo(&self, arguments: &Value, started: Instant) -> Result<ToolEnvelopeV2> {
        let graph = load_graph(self.root())?;
        let context_path = arguments.get("context_path").and_then(Value::as_str);
        let package =
            context_path.and_then(|path| packages_for_path(&graph, path).into_iter().next());
        let affected = package
            .as_deref()
            .map(|name| search_scope(&graph, name, false))
            .unwrap_or_default();
        let data = json!({
            "packages": graph.packages,
            "tasks": graph.tasks,
            "boundaries": graph.boundaries,
            "affected": affected,
            "provider": graph.provider,
            "version_probe": {
                "turbo_version": graph.turbo_version,
                "static_available": true,
                "documented_cli_probed": false,
                "internal_lsp_used": false,
            },
        });
        let mut metadata =
            SuccessMetadata::repository(OPTIONAL_TURBOREPO, "soleaux-native-turborepo-provider");
        metadata.cache_status = "live".to_string();
        Ok(ToolEnvelopeV2::success(
            self.workspace_id(),
            &self.root.to_string_lossy(),
            data,
            None,
            elapsed_us(started),
            metadata,
        ))
    }

    fn call_nextjs(&self, started: Instant) -> Result<ToolEnvelopeV2> {
        let route_index = index_nextjs(self.root())?;
        let data = json!({
            "applications": route_index.applications,
            "routes": route_index.routes,
            "server_actions": route_index.server_actions,
            "runtime": {
                "attached": route_index.runtime_evidence_attached,
                "capability_driven": true,
                "universal_get_routes_assumed": false,
            },
            "provider": route_index.provider,
        });
        let mut metadata =
            SuccessMetadata::repository(OPTIONAL_NEXTJS, "soleaux-native-next-provider");
        metadata.trust = "verified_code_structure".to_string();
        metadata.cache_status = "live".to_string();
        Ok(ToolEnvelopeV2::success(
            self.workspace_id(),
            &self.root.to_string_lossy(),
            data,
            None,
            elapsed_us(started),
            metadata,
        ))
    }

    pub async fn handle_json_rpc_async(&self, request: &Value) -> Option<Value> {
        let method = request
            .get("method")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let id = request.get("id").cloned()?;
        if !is_supported_rpc_method(method) {
            return Some(json_rpc_error(
                id,
                -32601,
                format!("unsupported method: {method}"),
            ));
        }
        let result: Result<Value> = match method {
            "initialize" => Ok(json!({
                "protocolVersion": negotiate_version(request.pointer("/params/protocolVersion").and_then(Value::as_str)),
                "capabilities": {"tools": {"listChanged": false}, "resources": {"listChanged": false, "subscribe": false}},
                "serverInfo": {"name":"Soleaux","version":profile::PRODUCT_VERSION}
            })),
            "ping" => Ok(json!({})),
            "resources/list" => Ok(json!({"resources":resource_definitions()})),
            "resources/read" => {
                self.read_resource(
                    request
                        .pointer("/params/uri")
                        .and_then(Value::as_str)
                        .unwrap_or_default(),
                )
                .await
            }
            "tools/list" => Ok(json!({
                "tools": self.tools().into_iter().map(tool_to_mcp).collect::<Vec<_>>()
            })),
            "tools/call" => {
                let name = request
                    .pointer("/params/name")
                    .and_then(Value::as_str)
                    .unwrap_or_default();
                let arguments = request
                    .pointer("/params/arguments")
                    .cloned()
                    .unwrap_or_else(|| json!({}));
                if let Err(error) = self.validate_tool_arguments(name, &arguments) {
                    return Some(json_rpc_error(id, -32602, error.to_string()));
                }
                let started = Instant::now();
                let envelope = match self.call_async(name, &arguments).await {
                    Ok(value) => value,
                    Err(error) => ToolEnvelopeV2::error(
                        self.workspace_id(),
                        &self.root.to_string_lossy(),
                        name,
                        ToolError {
                            error_type: "tool_execution_error".to_string(),
                            message: error.to_string(),
                            retryable: false,
                            details: json!({}),
                        },
                        elapsed_us(started),
                    ),
                };
                let is_error = envelope.status == "error";
                match serde_json::to_string(&envelope) {
                    Ok(encoded) => Ok(json!({
                        "content":[{"type":"text","text":encoded}],
                        "structuredContent":envelope,
                        "isError":is_error,
                    })),
                    Err(error) => Err(anyhow::anyhow!(
                        "failed to serialize tool response envelope: {error}"
                    )),
                }
            }
            _ => unreachable!("method was validated before dispatch"),
        };
        Some(match result {
            Ok(value) => json!({"jsonrpc":"2.0","id":id,"result":value}),
            Err(error) => json_rpc_error(id, -32602, error.to_string()),
        })
    }

    pub fn handle_json_rpc(&self, request: &Value) -> Option<Value> {
        let id = request.get("id").cloned()?;
        let method = request
            .get("method")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let result = match method {
            "initialize" => Ok(json!({
                "protocolVersion": negotiate_version(request.pointer("/params/protocolVersion").and_then(Value::as_str)),
                "capabilities": {"tools": {"listChanged": false}, "resources": {"listChanged": false, "subscribe": false}},
                "serverInfo": {"name":"Soleaux","version":profile::PRODUCT_VERSION}
            })),
            "ping" => Ok(json!({})),
            "resources/list" => Ok(json!({"resources":resource_definitions()})),
            "tools/list" => Ok(json!({
                "tools":self.tools().into_iter().map(tool_to_mcp).collect::<Vec<_>>()
            })),
            _ => Err(anyhow::anyhow!(
                "method requires the asynchronous native dispatcher: {method}"
            )),
        };
        Some(match result {
            Ok(value) => json!({"jsonrpc":"2.0","id":id,"result":value}),
            Err(error) => json_rpc_error(id, -32602, error.to_string()),
        })
    }

    pub async fn serve_stdio(&self) -> Result<()> {
        let stdin = tokio::io::stdin();
        let mut lines = BufReader::new(stdin).lines();
        let mut stdout = tokio::io::stdout();
        while let Some(line) = lines.next_line().await? {
            if line.trim().is_empty() {
                continue;
            }
            let response = match serde_json::from_str::<Value>(&line) {
                Ok(request) => self.handle_json_rpc_async(&request).await,
                Err(error) => Some(json!({
                    "jsonrpc":"2.0",
                    "id":Value::Null,
                    "error":{"code":-32700,"message":format!("invalid JSON-RPC request: {error}")}
                })),
            };
            if let Some(response) = response {
                stdout.write_all(&serde_json::to_vec(&response)?).await?;
                stdout.write_all(b"\n").await?;
                stdout.flush().await?;
            }
        }
        Ok(())
    }

    async fn read_resource(&self, uri: &str) -> Result<Value> {
        let value = match uri {
            "soleaux://status" => self.health(),
            "soleaux://about" => self.about(),
            "soleaux://contracts/unified-mcp-profile-v2" => {
                serde_json::from_str(profile::PROFILE_MANIFEST_JSON)?
            }
            "soleaux://contracts/context-packet-v2" => {
                serde_json::from_str(profile::CONTEXT_PACKET_SCHEMA_JSON)?
            }
            _ => bail!("unknown resource: {uri}"),
        };
        Ok(json!({"contents":[{
            "uri":uri,
            "mimeType":"application/json",
            "text":serde_json::to_string(&value)?
        }]}))
    }

    pub fn health(&self) -> Value {
        json!({
            "ok":true,
            "product":"Soleaux",
            "version":profile::PRODUCT_VERSION,
            "production_claim_allowed":profile::PRODUCTION_CLAIM_ALLOWED,
            "profile":"soleaux.mcp.profile/v2",
            "root_tool_count":self.tools().len(),
            "root_tool_ceiling":profile::HARD_CEILING,
            "workspace":self.root.to_string_lossy(),
            "store":self.index.store_stats().ok(),
        })
    }

    pub fn about(&self) -> Value {
        json!({
            "product":"Soleaux",
            "version":profile::PRODUCT_VERSION,
            "definition":"Unified native repository intelligence: one bounded MCP profile, one governed catalog, and provenance-tagged compiled context.",
            "runtime":"Rust/Tokio",
            "public_profile":self.active_tools.as_ref(),
            "hard_ceiling":profile::HARD_CEILING,
            "production_claim_allowed":profile::PRODUCTION_CLAIM_ALLOWED,
            "profile_digest":profile::PROFILE_MANIFEST_SHA256,
            "context_schema_digest":profile::CONTEXT_SCHEMA_SHA256,
        })
    }

    fn optional_provider_available(&self, name: &str) -> Result<bool> {
        match name {
            OPTIONAL_POSTGRES => Ok(true),
            OPTIONAL_TURBOREPO => {
                Ok(self.root.join("turbo.json").is_file() && load_graph(self.root()).is_ok())
            }
            OPTIONAL_NEXTJS => {
                Ok(index_nextjs(self.root()).is_ok_and(|index| !index.applications.is_empty()))
            }
            _ => Ok(false),
        }
    }

    fn resolve_context_resource(&self, uri: &str) -> Result<RequestedResource> {
        let (status, media_type, sha256, error) = match uri {
            "soleaux://status" => resolved_resource(&self.health())?,
            "soleaux://about" => resolved_resource(&self.about())?,
            "soleaux://contracts/unified-mcp-profile-v2" => resolved_resource(
                &serde_json::from_str::<Value>(profile::PROFILE_MANIFEST_JSON)?,
            )?,
            "soleaux://contracts/context-packet-v2" => {
                resolved_resource(&serde_json::from_str::<Value>(
                    profile::CONTEXT_PACKET_SCHEMA_JSON,
                )?)?
            }
            _ => (
                "unavailable".to_string(),
                None,
                None,
                Some("Resource URI is not registered by the native Soleaux server.".to_string()),
            ),
        };
        Ok(RequestedResource {
            uri: uri.to_string(),
            status,
            media_type,
            sha256,
            truncated: false,
            error,
        })
    }

    fn search_matches(
        &self,
        query: &str,
        paths: &[String],
        kinds: &[String],
        limit: usize,
    ) -> Result<SearchMatchesResult> {
        let mut matches = Vec::new();
        let mut observed = BTreeSet::new();
        let mut gaps = Vec::new();
        let structural_limit = limit.saturating_mul(4).clamp(1, 800);
        for hit in self.index.search_symbols(query, structural_limit)? {
            if !path_allowed(&hit.path, paths) || !kind_allowed(&hit.kind, kinds) {
                continue;
            }
            observed.insert(hit.path.clone());
            matches.push(symbol_match(self.workspace_id(), &hit));
            if matches.len() >= limit {
                break;
            }
        }
        if matches.len() < limit {
            let needle = query.to_ascii_lowercase();
            for file in self.index.files(10_000)? {
                if matches.len() >= limit {
                    break;
                }
                if !path_allowed(&file.path, paths) || !kind_allowed("text", kinds) {
                    continue;
                }
                let absolute = match self.index.resolve_existing_path(&file.path) {
                    Ok(value) => value,
                    Err(_) => continue,
                };
                match fs::metadata(&absolute) {
                    Ok(value) if value.len() <= 2 * 1024 * 1024 => {}
                    _ => continue,
                }
                let source = match fs::read_to_string(&absolute) {
                    Ok(value) => value,
                    Err(_) => continue,
                };
                let lowercase = source.to_ascii_lowercase();
                let Some(start) = lowercase.find(&needle) else {
                    continue;
                };
                let end = start.saturating_add(query.len()).min(source.len());
                let start_line = source[..start]
                    .bytes()
                    .filter(|byte| *byte == b'\n')
                    .count() as u64
                    + 1;
                let end_line = start_line
                    + source[start..end]
                        .bytes()
                        .filter(|byte| *byte == b'\n')
                        .count() as u64;
                let line = source
                    .lines()
                    .nth(start_line.saturating_sub(1) as usize)
                    .unwrap_or_default();
                let summary = utf8_prefix(line.trim(), 2048);
                let evidence_id = format!("text:{}:{start}", file.path);
                observed.insert(file.path.clone());
                matches.push(json!({
                    "kind":"text",
                    "table":"source.context",
                    "identity":format!("{}:{}",file.path,start_line),
                    "summary":if summary.is_empty(){format!("Text match in {}",file.path)}else{summary},
                    "path":file.path,
                    "start_line":start_line,
                    "end_line":end_line.max(start_line),
                    "start_byte":start,
                    "end_byte":end,
                    "symbol":Value::Null,
                    "score":0.25,
                    "evidence_id":evidence_id,
                    "relation_distance":0,
                    "trust":"retrieved_code_data",
                    "provenance":provenance(
                        "soleaux-native-bounded-text-search",
                        &file.engine,
                        Some(self.workspace_id()),
                        None,
                        Some(&file.path),
                        Some(&file.content_hash),
                        "utf8-bytes-zero-based",
                    ),
                }));
            }
        }
        if matches.is_empty() {
            gaps.push(gap(
                "no_search_matches",
                "No indexed structural or bounded textual match satisfied the query and filters.",
                "info",
                true,
                Some("repository.search"),
                None,
            ));
        }
        let truncated = matches.len() >= limit;
        Ok((matches, observed.into_iter().collect(), gaps, truncated))
    }

    fn symbols_data(&self, arguments: &Value) -> Result<SymbolsDataResult> {
        let limit = arguments
            .get("limit")
            .and_then(Value::as_u64)
            .unwrap_or(200)
            .clamp(1, 2_000) as usize;
        let include_source = arguments
            .get("include_source")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        let max_source = arguments
            .get("max_source_bytes_per_symbol")
            .and_then(Value::as_u64)
            .unwrap_or(0)
            .clamp(0, 65_536) as usize;
        let kinds = string_array(arguments, "kinds", 64)?;
        let requested = requested_symbol_paths(arguments)?;
        let files = if requested.is_empty() {
            self.index.files(256)?
        } else {
            requested
                .iter()
                .filter_map(|path| self.index.indexed_file(path).ok().flatten())
                .collect::<Vec<_>>()
        };
        let mut file_rows = Vec::new();
        let mut symbols = Vec::new();
        let mut evidence_values = Vec::new();
        let mut gaps = Vec::new();
        let mut observed = Vec::new();
        for file in &files {
            observed.push(file.path.clone());
            let file_symbols = self.index.symbols_for_file(&file.path)?;
            file_rows.push(json!({
                "path":file.path,
                "content_hash":file.content_hash,
                "language":file.language,
                "engine":file.engine,
                "symbol_count":file_symbols.len(),
            }));
            for symbol in file_symbols {
                if symbols.len() >= limit {
                    break;
                }
                if !kind_allowed(&symbol.kind, &kinds) {
                    continue;
                }
                let source = if include_source && max_source > 0 {
                    let start = usize::try_from(symbol.start_byte).unwrap_or(usize::MAX);
                    let end = usize::try_from(symbol.end_byte).unwrap_or(usize::MAX);
                    self.index
                        .read_source_range(&file.path, start, end, max_source)
                        .ok()
                } else {
                    None
                };
                let range_hash = source
                    .as_ref()
                    .map(|value| sha256_hex(value.as_bytes()))
                    .unwrap_or_else(|| {
                        sha256_hex(
                            format!(
                                "{}:{}:{}",
                                file.content_hash, symbol.start_byte, symbol.end_byte
                            )
                            .as_bytes(),
                        )
                    });
                let item = symbol_value(self.workspace_id(), file, &symbol, source, &range_hash);
                evidence_values.push(evidence(
                    format!("symbol:{}:{}", file.path, symbol.start_byte),
                    "symbol",
                    format!("{} {}", symbol.kind, symbol.name),
                    "verified_code_structure",
                    item.get("provenance").cloned().unwrap_or(Value::Null),
                    EvidenceRange {
                        path: Some(&file.path),
                        start_line: Some(symbol.start_row + 1),
                        end_line: Some(symbol.end_row + 1),
                        start_byte: Some(symbol.start_byte),
                        end_byte: Some(symbol.end_byte),
                    },
                ));
                symbols.push(item);
            }
        }
        for path in &requested {
            if !observed.contains(path) {
                gaps.push(gap(
                    "symbol_path_unavailable",
                    "Requested path is not present in the native structural index.",
                    "warning",
                    true,
                    Some("repository.symbols"),
                    Some(path),
                ));
            }
        }
        let truncated = symbols.len() >= limit;
        if truncated {
            gaps.push(gap(
                "symbol_limit_reached",
                "Additional symbols were omitted by the requested limit.",
                "info",
                true,
                Some("repository.symbols"),
                None,
            ));
        }
        let complete = gaps.is_empty();
        let gap_values = gaps.clone();
        let data = json!({
            "scope":{"path":arguments.get("path").cloned().unwrap_or(Value::Null),"paths":requested},
            "files":file_rows,
            "symbols":symbols,
            "coverage_complete":complete,
            "gaps":gaps,
        });
        Ok((
            data,
            evidence_values,
            complete,
            gap_values,
            observed,
            truncated,
        ))
    }
}

fn all_tool_definitions() -> &'static BTreeMap<String, ToolDefinition> {
    static DEFINITIONS: OnceLock<BTreeMap<String, ToolDefinition>> = OnceLock::new();
    DEFINITIONS.get_or_init(|| {
        let manifest: Value = serde_json::from_str(profile::PROFILE_MANIFEST_JSON)
            .expect("binding public profile JSON must parse");
        manifest
            .get("tools")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .chain(
                manifest
                    .get("optionalDefinitions")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten(),
            )
            .map(|value| {
                let name = value
                    .get("name")
                    .and_then(Value::as_str)
                    .expect("tool name")
                    .to_string();
                let definition = ToolDefinition {
                    name: name.clone(),
                    description: value
                        .get("description")
                        .and_then(Value::as_str)
                        .expect("tool description")
                        .to_string(),
                    input_schema: value.get("inputSchema").cloned().expect("input schema"),
                };
                (name, definition)
            })
            .collect()
    })
}

fn validate_active_profile(active: &[String]) -> Result<()> {
    if active.len() != profile::HARD_CEILING {
        bail!("active public profile must contain exactly twelve slots");
    }
    if active.iter().collect::<BTreeSet<_>>().len() != active.len() {
        bail!("active public profile contains duplicate tools");
    }
    let allowed = profile::CANONICAL_TOOL_NAMES
        .iter()
        .chain(profile::OPTIONAL_TOOL_NAMES.iter())
        .copied()
        .collect::<BTreeSet<_>>();
    if let Some(unknown) = active.iter().find(|name| !allowed.contains(name.as_str())) {
        bail!("active public profile contains an unknown tool: {unknown}");
    }
    Ok(())
}

fn tool_to_mcp(tool: ToolDefinition) -> Value {
    json!({"name":tool.name,"description":tool.description,"inputSchema":tool.input_schema})
}

fn resource_definitions() -> Vec<Value> {
    vec![
        json!({"uri":"soleaux://status","name":"Soleaux status","description":"Native server, profile, and index health","mimeType":"application/json"}),
        json!({"uri":"soleaux://about","name":"About Soleaux","description":"Product and contract identity","mimeType":"application/json"}),
        json!({"uri":"soleaux://contracts/unified-mcp-profile-v2","name":"Unified MCP profile v2","description":"Binding twelve-slot public profile","mimeType":"application/json"}),
        json!({"uri":"soleaux://contracts/context-packet-v2","name":"Context packet v2 schema","description":"Binding compiled-context schema","mimeType":"application/json"}),
    ]
}

fn requires_fresh_repository_state(tool: &str) -> bool {
    matches!(
        tool,
        "context.compile"
            | "code.search"
            | "get_symbols"
            | "registry.list"
            | "registry.read"
            | "repo_info"
    )
}

fn is_supported_rpc_method(method: &str) -> bool {
    matches!(
        method,
        "initialize" | "ping" | "resources/list" | "resources/read" | "tools/list" | "tools/call"
    )
}

fn json_rpc_error(id: Value, code: i64, message: impl Into<String>) -> Value {
    json!({"jsonrpc":"2.0","id":id,"error":{"code":code,"message":message.into()}})
}

fn negotiate_version(requested: Option<&str>) -> &'static str {
    match requested {
        Some(MCP_EXPERIMENTAL_VERSION) => MCP_EXPERIMENTAL_VERSION,
        _ => MCP_STABLE_VERSION,
    }
}

fn required_string<'a>(arguments: &'a Value, name: &str) -> Result<&'a str> {
    arguments
        .get(name)
        .and_then(Value::as_str)
        .with_context(|| format!("missing string argument: {name}"))
}

fn string_array(arguments: &Value, name: &str, maximum: usize) -> Result<Vec<String>> {
    let values = arguments
        .get(name)
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    if values.len() > maximum {
        bail!("{name} exceeded its cardinality limit of {maximum}");
    }
    let output = values
        .into_iter()
        .map(|value| {
            value
                .as_str()
                .map(str::to_string)
                .with_context(|| format!("{name} must contain strings"))
        })
        .collect::<Result<Vec<_>>>()?;
    if output.iter().collect::<BTreeSet<_>>().len() != output.len() {
        bail!("{name} must contain unique values");
    }
    Ok(output)
}

fn requested_symbol_paths(arguments: &Value) -> Result<Vec<String>> {
    let mut values = string_array(arguments, "paths", 256)?;
    if let Some(path) = arguments.get("path").and_then(Value::as_str) {
        if !values.is_empty() {
            bail!("get_symbols accepts path or paths, not both");
        }
        values.push(path.to_string());
    }
    Ok(values)
}

fn path_allowed(path: &str, scopes: &[String]) -> bool {
    scopes.is_empty()
        || scopes
            .iter()
            .any(|scope| path == scope || path.starts_with(&format!("{scope}/")))
}

fn kind_allowed(kind: &str, kinds: &[String]) -> bool {
    kinds.is_empty() || kinds.iter().any(|selected| selected == kind)
}

fn symbol_match(workspace_id: Uuid, hit: &SymbolHit) -> Value {
    json!({
        "kind":hit.kind,
        "table":"repository.symbols",
        "identity":format!("{}:{}:{}",hit.path,hit.kind,hit.name),
        "summary":format!("{} {} in {}",hit.kind,hit.name,hit.path),
        "path":hit.path,
        "start_line":hit.start_row + 1,
        "end_line":hit.end_row + 1,
        "start_byte":hit.start_byte,
        "end_byte":hit.end_byte,
        "symbol":hit.name,
        "score":hit.score,
        "evidence_id":format!("symbol:{}:{}",hit.path,hit.start_byte),
        "relation_distance":0,
        "trust":"verified_code_structure",
        "provenance":provenance(
            "soleaux-native-structural-index",
            "sqlite-fts5+native-parser",
            Some(workspace_id),
            None,
            Some(&hit.path),
            None,
            "utf8-bytes-zero-based",
        ),
    })
}

fn symbol_value(
    workspace_id: Uuid,
    file: &IndexedFileRecord,
    symbol: &SymbolRecord,
    source: Option<String>,
    range_hash: &str,
) -> Value {
    let mut provenance_value = provenance(
        "soleaux-native-structural-index",
        &file.engine,
        Some(workspace_id),
        None,
        Some(&file.path),
        Some(&file.content_hash),
        "utf8-bytes-zero-based",
    );
    if let Some(object) = provenance_value.as_object_mut() {
        object.insert(
            "source_range_hash".to_string(),
            Value::String(range_hash.to_string()),
        );
    }
    json!({
        "name":symbol.name,
        "kind":symbol.kind,
        "path":file.path,
        "container":Value::Null,
        "detail":Value::Null,
        "start_line":symbol.start_row + 1,
        "end_line":symbol.end_row + 1,
        "start_byte":symbol.start_byte,
        "end_byte":symbol.end_byte,
        "source":source,
        "file_content_hash":file.content_hash,
        "source_range_hash":range_hash,
        "trust":"verified_code_structure",
        "provenance":provenance_value,
    })
}

fn preview_data(preview: &StoredPreview) -> Value {
    json!({
        "preview_id":preview.preview_id,
        "digest":preview.digest,
        "created_at_unix_ms":preview.created_at_unix_ms,
        "expires_at_unix_ms":preview.expires_at_unix_ms,
        "operation":preview.operation,
        "patches":preview.patches,
        "non_overlapping":preview.non_overlapping,
        "writes_performed":false,
        "validation_plan":preview.validation_plan,
        "warnings":preview.warnings,
    })
}

fn context_evidence(packet: &ContextPacketV2) -> Vec<Value> {
    packet
        .sources
        .iter()
        .chain(packet.canonical_owners.iter())
        .chain(packet.consumers.iter())
        .chain(packet.constraints.iter())
        .chain(packet.conflicts.iter())
        .chain(packet.validation_routes.iter())
        .chain(packet.supporting_facts.iter())
        .take(4096)
        .map(|item| {
            evidence(
                item.evidence_id.clone(),
                item.section.clone(),
                item.summary.clone(),
                &item.trust,
                serde_json::to_value(&item.provenance).unwrap_or(Value::Null),
                EvidenceRange {
                    path: Some(&item.path),
                    start_line: Some(item.start_line),
                    end_line: Some(item.end_line),
                    start_byte: item.start_byte,
                    end_byte: item.end_byte,
                },
            )
        })
        .collect()
}

fn context_suggestions(packet: &ContextPacketV2) -> Vec<Value> {
    let mut suggestions = Vec::new();
    if let Some(path) = packet.sources.first().map(|item| item.path.clone()) {
        suggestions.push(json!({"tool":"get_symbols","args":{"path":path,"limit":100}}));
    }
    if !packet.gaps.is_empty() {
        suggestions.push(
            json!({"tool":"registry.read","args":{"tables":["ownership","frameworks"],"limit":50}}),
        );
    }
    suggestions
}

fn search_evidence(matches: &[Value]) -> Vec<Value> {
    matches
        .iter()
        .filter_map(|item| {
            Some(evidence(
                item.get("evidence_id")?.as_str()?.to_string(),
                item.get("kind")?.as_str()?.to_string(),
                item.get("summary")?.as_str()?.to_string(),
                item.get("trust")?.as_str()?,
                item.get("provenance")?.clone(),
                EvidenceRange {
                    path: item.get("path").and_then(Value::as_str),
                    start_line: item.get("start_line").and_then(Value::as_u64),
                    end_line: item.get("end_line").and_then(Value::as_u64),
                    start_byte: item.get("start_byte").and_then(Value::as_u64),
                    end_byte: item.get("end_byte").and_then(Value::as_u64),
                },
            ))
        })
        .collect()
}

fn cursor_offset(cursor: Option<&str>) -> Result<usize> {
    match cursor {
        None => Ok(0),
        Some(value) => value
            .strip_prefix("offset:")
            .context("cursor has invalid framing")?
            .parse::<usize>()
            .context("cursor offset is invalid"),
    }
}

fn resolved_resource(value: &Value) -> Result<ResolvedResourceResult> {
    let bytes = serde_json::to_vec(value)?;
    Ok((
        "resolved".to_string(),
        Some("application/json".to_string()),
        Some(sha256_hex(&bytes)),
        None,
    ))
}

fn sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    format!("{:x}", hasher.finalize())
}

fn elapsed_us(started: Instant) -> u64 {
    u64::try_from(started.elapsed().as_micros()).unwrap_or(u64::MAX)
}

fn utf8_prefix(value: &str, maximum: usize) -> String {
    if value.len() <= maximum {
        return value.to_string();
    }
    let mut end = maximum;
    while !value.is_char_boundary(end) {
        end = end.saturating_sub(1);
    }
    value[..end].to_string()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::tempdir;

    #[test]
    fn locked_tool_input_schemas_are_supported_and_closed() {
        for definition in all_tool_definitions().values() {
            schema::validate_schema_definition(&definition.input_schema).unwrap_or_else(|error| {
                panic!("{} has unsupported input schema: {error}", definition.name)
            });
            assert_eq!(
                definition.input_schema.get("type").and_then(Value::as_str),
                Some("object"),
                "{} input schema must be an object",
                definition.name
            );
            assert_eq!(
                definition
                    .input_schema
                    .get("additionalProperties")
                    .and_then(Value::as_bool),
                Some(false),
                "{} input schema must reject unknown arguments",
                definition.name
            );
        }
    }

    #[tokio::test]
    async fn invalid_tool_arguments_fail_before_dispatch() {
        let temp = tempdir().expect("tempdir");
        let server = PublicMcpServer::with_store(temp.path(), temp.path().join("index.sqlite3"))
            .expect("server");
        let invalid = [
            ("context.compile", json!({})),
            ("repo_info", json!({"unknown": true})),
            ("code.search", json!({"query": "x", "limit": 0})),
            ("repo_info", json!([])),
        ];
        for (name, arguments) in invalid {
            let error = server
                .call_async(name, &arguments)
                .await
                .err()
                .unwrap_or_else(|| panic!("{name} unexpectedly accepted invalid arguments"));
            assert!(
                error.to_string().contains("invalid arguments for"),
                "unexpected validation error for {name}: {error}"
            );
        }
    }

    #[tokio::test]
    async fn json_rpc_invalid_tool_arguments_return_invalid_params() {
        let temp = tempdir().expect("tempdir");
        let server = PublicMcpServer::with_store(temp.path(), temp.path().join("index.sqlite3"))
            .expect("server");
        for arguments in [
            json!({}),
            json!({"objective": "inspect", "unknown": true}),
            json!({"objective": "inspect", "limit": 0}),
        ] {
            let response = server
                .handle_json_rpc_async(&json!({
                    "jsonrpc": "2.0",
                    "id": 17,
                    "method": "tools/call",
                    "params": {"name": "context.compile", "arguments": arguments}
                }))
                .await
                .expect("response");
            assert_eq!(
                response.pointer("/error/code").and_then(Value::as_i64),
                Some(-32602)
            );
            assert!(
                response
                    .pointer("/error/message")
                    .and_then(Value::as_str)
                    .is_some_and(
                        |message| message.contains("invalid arguments for context.compile")
                    )
            );
            assert!(response.get("result").is_none());
        }
    }

    #[tokio::test]
    async fn structural_reads_refresh_external_mutations_and_deletions() {
        let temp = tempdir().expect("tempdir");
        fs::create_dir_all(temp.path().join("src")).expect("src");
        let source_path = temp.path().join("src/state.ts");
        fs::write(&source_path, "export function oldState() { return 'old'; }")
            .expect("old fixture");
        let server = PublicMcpServer::with_store(temp.path(), temp.path().join("index.sqlite3"))
            .expect("server");
        server.prepare().await.expect("prepare");

        let old_before = server
            .call_async("code.search", &json!({"query": "oldState"}))
            .await
            .expect("old search");
        assert!(
            old_before
                .data
                .get("matches")
                .and_then(Value::as_array)
                .is_some_and(|matches| !matches.is_empty())
        );

        fs::write(&source_path, "export function newState() { return 'new'; }")
            .expect("external mutation");
        let new_after = server
            .call_async("code.search", &json!({"query": "newState"}))
            .await
            .expect("new search");
        assert!(
            new_after
                .data
                .get("matches")
                .and_then(Value::as_array)
                .is_some_and(|matches| !matches.is_empty())
        );
        let old_after = server
            .call_async("code.search", &json!({"query": "oldState"}))
            .await
            .expect("old search after mutation");
        assert!(
            old_after
                .data
                .get("matches")
                .and_then(Value::as_array)
                .is_some_and(Vec::is_empty)
        );

        fs::remove_file(&source_path).expect("external deletion");
        let deleted = server
            .call_async("get_symbols", &json!({"path": "src/state.ts"}))
            .await
            .expect("deleted symbols");
        assert!(
            deleted
                .data
                .get("symbols")
                .and_then(Value::as_array)
                .is_some_and(Vec::is_empty)
        );
        assert!(
            deleted
                .coverage
                .as_ref()
                .and_then(|coverage| coverage.get("complete"))
                .and_then(Value::as_bool)
                == Some(false)
        );
    }

    #[tokio::test]
    async fn concurrent_structural_reads_share_a_serial_refresh_barrier() {
        let temp = tempdir().expect("tempdir");
        fs::create_dir_all(temp.path().join("src")).expect("src");
        fs::write(
            temp.path().join("src/concurrent.ts"),
            "export function concurrentState() { return true; }",
        )
        .expect("fixture");
        let server = PublicMcpServer::with_store(temp.path(), temp.path().join("index.sqlite3"))
            .expect("server");
        server.prepare().await.expect("prepare");
        let left = server.clone();
        let right = server.clone();
        let (left_result, right_result) = tokio::join!(
            async move {
                left.call_async("code.search", &json!({"query": "concurrentState"}))
                    .await
            },
            async move {
                right
                    .call_async("get_symbols", &json!({"path": "src/concurrent.ts"}))
                    .await
            }
        );
        assert!(left_result.is_ok());
        assert!(right_result.is_ok());
    }

    #[tokio::test]
    async fn canonical_profile_is_exactly_twelve_in_locked_order() {
        let temp = tempdir().expect("tempdir");
        fs::create_dir_all(temp.path().join("src")).expect("src");
        fs::write(
            temp.path().join("src/lib.ts"),
            "export function hello() { return true; }",
        )
        .expect("fixture");
        let server = PublicMcpServer::with_store(temp.path(), temp.path().join("index.sqlite3"))
            .expect("server");
        server.prepare().await.expect("prepare");
        let names = server
            .tools()
            .into_iter()
            .map(|tool| tool.name)
            .collect::<Vec<_>>();
        assert_eq!(
            names,
            profile::CANONICAL_TOOL_NAMES
                .iter()
                .map(|name| (*name).to_string())
                .collect::<Vec<_>>()
        );
        assert_eq!(names.len(), 12);
    }

    #[tokio::test]
    async fn explicit_substitution_preserves_slot_order_and_ceiling() {
        let temp = tempdir().expect("tempdir");
        fs::write(
            temp.path().join("Cargo.toml"),
            "[package]\nname='fixture'\nversion='0.1.0'\n",
        )
        .expect("fixture");
        let server = PublicMcpServer::with_store(temp.path(), temp.path().join("index.sqlite3"))
            .expect("server")
            .substitute_tool("restart_lsp", OPTIONAL_POSTGRES)
            .expect("substitution");
        server.prepare().await.expect("prepare");
        let names = server
            .tools()
            .into_iter()
            .map(|tool| tool.name)
            .collect::<Vec<_>>();
        assert_eq!(names.len(), 12);
        assert_eq!(names[11], OPTIONAL_POSTGRES);
        assert!(!names.contains(&"restart_lsp".to_string()));
    }

    #[tokio::test]
    async fn context_compile_returns_v2_typed_packet() {
        let temp = tempdir().expect("tempdir");
        fs::create_dir_all(temp.path().join("src")).expect("src");
        fs::create_dir_all(temp.path().join(".github")).expect("github");
        fs::write(
            temp.path().join("src/context.ts"),
            "export function compileContext(task: string) { return task; }",
        )
        .expect("fixture");
        fs::write(
            temp.path().join(".github/CODEOWNERS"),
            "src/** @soleaux/core\n",
        )
        .expect("owners");
        fs::write(
            temp.path().join("AGENTS.md"),
            "# Constraints\nKeep public tools bounded.\n",
        )
        .expect("constraints");
        let server = PublicMcpServer::with_store(temp.path(), temp.path().join("index.sqlite3"))
            .expect("server");
        server.prepare().await.expect("prepare");
        let envelope = server
            .call_async(
                "context.compile",
                &json!({"objective":"update compileContext","paths":["src/context.ts"]}),
            )
            .await
            .expect("context");
        assert_eq!(envelope.status, "ok");
        assert_eq!(
            envelope.data.get("schema_version").and_then(Value::as_str),
            Some("soleaux.context/v2")
        );
        assert!(
            envelope
                .data
                .get("canonical_owners")
                .and_then(Value::as_array)
                .is_some_and(|items| !items.is_empty())
        );
        assert!(
            envelope
                .data
                .get("constraints")
                .and_then(Value::as_array)
                .is_some_and(|items| !items.is_empty())
        );
        assert_eq!(
            envelope
                .data
                .pointer("/native/selected_parsers_native")
                .and_then(Value::as_bool),
            Some(true)
        );
        assert_eq!(
            envelope
                .data
                .pointer("/native/selected_lsps_native")
                .and_then(Value::as_bool),
            Some(true)
        );
    }

    #[tokio::test]
    async fn structural_preview_is_no_write_and_edit_is_hash_bound() {
        let temp = tempdir().expect("tempdir");
        fs::create_dir_all(temp.path().join("src")).expect("src");
        let path = temp.path().join("src/lib.ts");
        fs::write(&path, "export const value = 1;\n").expect("fixture");
        let server = PublicMcpServer::with_store(temp.path(), temp.path().join("index.sqlite3"))
            .expect("server");
        server.prepare().await.expect("prepare");
        let preview = server
            .call_async(
                "preview",
                &json!({
                    "operation":"structural_rewrite",
                    "paths":["src/lib.ts"],
                    "structural":{"search":"value = 1","replacement":"value = 2"}
                }),
            )
            .await
            .expect("preview");
        assert_eq!(
            fs::read_to_string(&path).expect("read"),
            "export const value = 1;\n"
        );
        let applied = server
            .call_async(
                "edit",
                &json!({
                    "preview_id":preview.data.get("preview_id").and_then(Value::as_str).unwrap(),
                    "digest":preview.data.get("digest").and_then(Value::as_str).unwrap(),
                    "confirm":true
                }),
            )
            .await
            .expect("edit");
        assert_eq!(
            applied.data.get("applied").and_then(Value::as_bool),
            Some(true)
        );
        assert_eq!(
            fs::read_to_string(&path).expect("read"),
            "export const value = 2;\n"
        );
    }

    #[test]
    fn unknown_or_implicit_profiles_fail_closed() {
        assert!(validate_active_profile(&["context.compile".to_string()]).is_err());
        let mut names = profile::CANONICAL_TOOL_NAMES
            .iter()
            .map(|name| (*name).to_string())
            .collect::<Vec<_>>();
        names[0] = "unknown".to_string();
        assert!(validate_active_profile(&names).is_err());
    }
}
