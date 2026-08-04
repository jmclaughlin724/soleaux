//! Native LSP-backed navigation, inspection, editor preview, and restart paths.

use crate::envelope::{coverage, gap};
use anyhow::{Context, Result, bail};
use serde_json::{Value, json};
use soleaux_intelligence::{
    index::RepositoryIndex,
    lsp::{LspProbe, LspQuery, LspQueryResult, LspSupervisor, capability_property},
};
use std::{
    collections::{BTreeSet, HashMap},
    fs,
    path::Path,
    sync::Arc,
};
use tokio::sync::RwLock;
use url::Url;
use uuid::Uuid;

#[derive(Clone)]
pub struct SemanticService {
    index: RepositoryIndex,
    lsp: LspSupervisor,
    language_servers: Arc<RwLock<HashMap<String, String>>>,
    probes: Arc<RwLock<Vec<LspProbe>>>,
}

#[derive(Debug, Clone)]
pub struct SemanticResponse {
    pub data: Value,
    pub cache_status: String,
    pub coverage: Value,
    pub warnings: Vec<String>,
}

impl SemanticService {
    pub fn new(
        index: RepositoryIndex,
        lsp: LspSupervisor,
        language_servers: Arc<RwLock<HashMap<String, String>>>,
        probes: Arc<RwLock<Vec<LspProbe>>>,
    ) -> Self {
        Self {
            index,
            lsp,
            language_servers,
            probes,
        }
    }

    pub async fn probes(&self) -> Vec<LspProbe> {
        self.probes.read().await.clone()
    }

    pub async fn navigate(&self, arguments: &Value) -> Result<SemanticResponse> {
        let operation = required_string(arguments, "operation")?;
        let target = self.resolve_target(arguments)?;
        let method = match operation {
            "definition" => "textDocument/definition",
            "references" => "textDocument/references",
            "implementation" => "textDocument/implementation",
            "hover" => "textDocument/hover",
            "call_hierarchy" | "incoming_calls" | "outgoing_calls" => {
                "textDocument/prepareCallHierarchy"
            }
            _ => bail!("unsupported navigate operation: {operation}"),
        };
        let semantic_mode = arguments
            .get("semantic_mode")
            .and_then(Value::as_str)
            .unwrap_or("best_available");
        let Some(server_id) = self.server_for_path(&target.path).await? else {
            return self.unavailable_navigation(
                operation,
                semantic_mode,
                &target.path,
                SemanticUnavailability::NoNativeServer,
            );
        };
        if !self.lsp.supports(&server_id, method).await? {
            return self.unavailable_navigation(
                operation,
                semantic_mode,
                &target.path,
                SemanticUnavailability::MissingCapability {
                    server_id: &server_id,
                    capability: capability_property(method).unwrap_or(method),
                },
            );
        }
        let (uri, file, text) = self.open_target(&server_id, &target.path).await?;
        let params = match operation {
            "references" => json!({
                "textDocument":{"uri":uri},
                "position":{"line":target.line_zero,"character":target.column_zero},
                "context":{"includeDeclaration":true},
            }),
            _ => json!({
                "textDocument":{"uri":uri},
                "position":{"line":target.line_zero,"character":target.column_zero},
            }),
        };
        let first = self
            .query(
                &server_id,
                method,
                params,
                &file.content_hash,
                &target.path,
                Some(1),
            )
            .await?;
        let response = if matches!(operation, "incoming_calls" | "outgoing_calls") {
            match first {
                LspQueryResult::Ready { value, .. } => {
                    let item = value
                        .as_array()
                        .and_then(|items| items.first())
                        .cloned()
                        .context("call hierarchy preparation returned no item")?;
                    let second_method = if operation == "incoming_calls" {
                        "callHierarchy/incomingCalls"
                    } else {
                        "callHierarchy/outgoingCalls"
                    };
                    self.query(
                        &server_id,
                        second_method,
                        json!({"item":item}),
                        &file.content_hash,
                        &target.path,
                        Some(1),
                    )
                    .await?
                }
                pending => pending,
            }
        } else {
            first
        };
        let data = normalize_navigation(
            operation,
            response,
            self.index.root(),
            Some(&server_id),
            Some(1),
        )?;
        let cache_status = if data.get("pending").and_then(Value::as_bool) == Some(true) {
            "pending"
        } else {
            "live"
        };
        let coverage_value = coverage(
            true,
            vec![target.path.clone()],
            vec![target.path],
            Vec::new(),
            vec![format!("lsp:{server_id}")],
            Vec::new(),
            None,
        );
        let _ = text;
        Ok(SemanticResponse {
            data,
            cache_status: cache_status.to_string(),
            coverage: coverage_value,
            warnings: Vec::new(),
        })
    }

    pub async fn inspect(&self, arguments: &Value) -> Result<SemanticResponse> {
        let operation = required_string(arguments, "operation")?;
        let path = required_string(arguments, "path")?;
        let line = required_u64(arguments, "line")?;
        let column = required_u64(arguments, "column")?;
        let method = match operation {
            "diagnostics" => "textDocument/diagnostic",
            "completion" => "textDocument/completion",
            "signature_help" => "textDocument/signatureHelp",
            "code_actions" => "textDocument/codeAction",
            _ => bail!("unsupported inspect operation: {operation}"),
        };
        let semantic_mode = arguments
            .get("semantic_mode")
            .and_then(Value::as_str)
            .unwrap_or("best_available");
        let Some(server_id) = self.server_for_path(path).await? else {
            return self.unavailable_inspection(
                operation,
                semantic_mode,
                path,
                SemanticUnavailability::NoNativeServer,
            );
        };
        if !self.lsp.supports(&server_id, method).await? {
            return self.unavailable_inspection(
                operation,
                semantic_mode,
                path,
                SemanticUnavailability::MissingCapability {
                    server_id: &server_id,
                    capability: capability_property(method).unwrap_or(method),
                },
            );
        }
        let (uri, file, _text) = self.open_target(&server_id, path).await?;
        let line_zero = line.saturating_sub(1);
        let column_zero = column.saturating_sub(1);
        let params = match operation {
            "diagnostics" => json!({"textDocument":{"uri":uri},"identifier":"soleaux"}),
            "code_actions" => json!({
                "textDocument":{"uri":uri},
                "range":{
                    "start":{"line":line_zero,"character":column_zero},
                    "end":{"line":line_zero,"character":column_zero},
                },
                "context":{"diagnostics":[]},
            }),
            _ => {
                json!({"textDocument":{"uri":uri},"position":{"line":line_zero,"character":column_zero}})
            }
        };
        let result = self
            .query(
                &server_id,
                method,
                params,
                &file.content_hash,
                path,
                Some(1),
            )
            .await?;
        let data = normalize_inspection(operation, result, Some(&server_id), Some(1));
        let cache_status = if data.get("pending").and_then(Value::as_bool) == Some(true) {
            "pending"
        } else {
            "live"
        };
        Ok(SemanticResponse {
            data,
            cache_status: cache_status.to_string(),
            coverage: coverage(
                true,
                vec![path.to_string()],
                vec![path.to_string()],
                Vec::new(),
                vec![format!("lsp:{server_id}")],
                Vec::new(),
                None,
            ),
            warnings: Vec::new(),
        })
    }

    pub async fn preview_workspace_edit(&self, arguments: &Value) -> Result<(String, Value)> {
        let operation = required_string(arguments, "operation")?;
        let path = required_string(arguments, "path")?;
        let Some(server_id) = self.server_for_path(path).await? else {
            bail!("no native LSP completed its capability probe for {path}");
        };
        let (uri, file, _text) = self.open_target(&server_id, path).await?;
        let query = match operation {
            "rename" => {
                let target = self.resolve_target(arguments)?;
                let new_name = required_string(arguments, "new_name")?;
                (
                    "textDocument/rename",
                    json!({
                        "textDocument":{"uri":uri},
                        "position":{"line":target.line_zero,"character":target.column_zero},
                        "newName":new_name,
                    }),
                )
            }
            "format_document" => (
                "textDocument/formatting",
                json!({"textDocument":{"uri":uri},"options":{"tabSize":2,"insertSpaces":true}}),
            ),
            "format_range" => {
                let line = required_u64(arguments, "line")?.saturating_sub(1);
                let column = required_u64(arguments, "column")?.saturating_sub(1);
                let end_line = required_u64(arguments, "end_line")?.saturating_sub(1);
                let end_column = required_u64(arguments, "end_column")?.saturating_sub(1);
                (
                    "textDocument/rangeFormatting",
                    json!({
                        "textDocument":{"uri":uri},
                        "range":{
                            "start":{"line":line,"character":column},
                            "end":{"line":end_line,"character":end_column},
                        },
                        "options":{"tabSize":2,"insertSpaces":true},
                    }),
                )
            }
            "code_action" => {
                let line = required_u64(arguments, "line")?.saturating_sub(1);
                let column = required_u64(arguments, "column")?.saturating_sub(1);
                let end_line = arguments
                    .get("end_line")
                    .and_then(Value::as_u64)
                    .unwrap_or(line + 1)
                    .saturating_sub(1);
                let end_column = arguments
                    .get("end_column")
                    .and_then(Value::as_u64)
                    .unwrap_or(column + 1)
                    .saturating_sub(1);
                (
                    "textDocument/codeAction",
                    json!({
                        "textDocument":{"uri":uri},
                        "range":{
                            "start":{"line":line,"character":column},
                            "end":{"line":end_line,"character":end_column},
                        },
                        "context":{"diagnostics":[]},
                    }),
                )
            }
            _ => bail!("operation does not use an LSP workspace edit: {operation}"),
        };
        let result = self
            .query(
                &server_id,
                query.0,
                query.1,
                &file.content_hash,
                path,
                Some(1),
            )
            .await?;
        let value = match result {
            LspQueryResult::Ready { value, .. } => value,
            LspQueryResult::Pending { request_id, .. } => {
                bail!("LSP preview is pending; retry after request {request_id} completes")
            }
        };
        let workspace_edit = match operation {
            "rename" => value,
            "format_document" | "format_range" => json!({"changes":{uri:value}}),
            "code_action" => {
                let index = arguments
                    .get("action_index")
                    .and_then(Value::as_u64)
                    .context("code_action requires action_index")?
                    as usize;
                let action = value
                    .as_array()
                    .and_then(|items| items.get(index))
                    .context("code_action index is outside the returned action list")?;
                action
                    .get("edit")
                    .cloned()
                    .context("selected code action did not include a WorkspaceEdit")?
            }
            _ => unreachable!(),
        };
        Ok((server_id, workspace_edit))
    }

    pub async fn restart(&self, arguments: &Value) -> Result<Value> {
        let provider = arguments.get("provider").and_then(Value::as_str);
        let language = arguments.get("language").and_then(Value::as_str);
        let path = arguments.get("path").and_then(Value::as_str);
        let probes = self.probes().await;
        let mut targets = BTreeSet::new();
        if let Some(provider) = provider
            && probes.iter().any(|probe| probe.server_id == provider)
        {
            targets.insert(provider.to_string());
        }
        if let Some(language) = language
            && let Some(server_id) = self.language_servers.read().await.get(language).cloned()
        {
            targets.insert(server_id);
        }
        if let Some(path) = path
            && let Some(server_id) = self.server_for_path(path).await?
        {
            targets.insert(server_id);
        }
        if provider.is_none() && language.is_none() && path.is_none() {
            targets.extend(probes.iter().map(|probe| probe.server_id.clone()));
        }
        if targets.is_empty() {
            bail!("restart_lsp selected no running native language-server session");
        }
        let mut restarted = Vec::new();
        let mut failures = Vec::new();
        for server_id in targets {
            match self.lsp.restart(&server_id).await {
                Ok(_) => restarted.push(server_id),
                Err(error) => {
                    failures.push(json!({"server_id":server_id,"message":error.to_string()}))
                }
            }
        }
        Ok(json!({
            "receipt_id": Uuid::now_v7().to_string(),
            "restarted": restarted,
            "skipped": [],
            "failures": failures,
            "process_mutated": true,
        }))
    }

    async fn query(
        &self,
        server_id: &str,
        method: &str,
        params: Value,
        content_hash: &str,
        path: &str,
        version: Option<i64>,
    ) -> Result<LspQueryResult> {
        self.lsp
            .query(LspQuery {
                workspace_id: self.index.workspace_id(),
                server_id: server_id.to_string(),
                method: method.to_string(),
                params,
                cache_key: format!("{content_hash}:{path}:{method}"),
                document_uri: None,
                document_version: version,
            })
            .await
    }

    async fn open_target(
        &self,
        server_id: &str,
        relative: &str,
    ) -> Result<(String, soleaux_storage::IndexedFileRecord, String)> {
        if !self.index.validate_indexed_file(relative)? {
            bail!("semantic target changed since it was indexed: {relative}");
        }
        let file = self
            .index
            .indexed_file(relative)?
            .with_context(|| format!("file is not in the structural index: {relative}"))?;
        let absolute = self.index.resolve_existing_path(relative)?;
        let uri = Url::from_file_path(&absolute)
            .map_err(|_| anyhow::anyhow!("unable to convert source path to file URI"))?
            .to_string();
        let text = fs::read_to_string(&absolute)
            .with_context(|| format!("reading semantic document {}", absolute.display()))?;
        let language_id = language_key(&file.language);
        self.lsp
            .open_document(server_id, &uri, language_id, 1, &text)
            .await?;
        Ok((uri, file, text))
    }

    async fn server_for_path(&self, relative: &str) -> Result<Option<String>> {
        let file = self
            .index
            .indexed_file(relative)?
            .with_context(|| format!("file is not in the structural index: {relative}"))?;
        Ok(self
            .language_servers
            .read()
            .await
            .get(language_key(&file.language))
            .cloned())
    }

    fn resolve_target(&self, arguments: &Value) -> Result<SemanticTarget> {
        if let Some(symbol_name) = arguments.get("symbol_name").and_then(Value::as_str) {
            let path_filter = arguments.get("path").and_then(Value::as_str);
            let kind_filter = arguments.get("symbol_kind").and_then(Value::as_str);
            let matches = self
                .index
                .search_symbols(symbol_name, 200)?
                .into_iter()
                .filter(|hit| hit.name == symbol_name)
                .filter(|hit| path_filter.is_none_or(|path| path == hit.path))
                .filter(|hit| kind_filter.is_none_or(|kind| kind == hit.kind))
                .collect::<Vec<_>>();
            if matches.len() != 1 {
                bail!(
                    "symbol target must resolve uniquely; observed {} matches",
                    matches.len()
                );
            }
            let hit = &matches[0];
            return Ok(SemanticTarget {
                path: hit.path.clone(),
                line_zero: hit.start_row,
                column_zero: 0,
            });
        }
        let path = required_string(arguments, "path")?.to_string();
        let line_zero = required_u64(arguments, "line")?.saturating_sub(1);
        let column_zero = required_u64(arguments, "column")?.saturating_sub(1);
        Ok(SemanticTarget {
            path,
            line_zero,
            column_zero,
        })
    }

    fn unavailable_navigation(
        &self,
        operation: &str,
        semantic_mode: &str,
        path: &str,
        cause: SemanticUnavailability<'_>,
    ) -> Result<SemanticResponse> {
        if semantic_mode == "semantic_required" {
            bail!("{}", cause.required_message());
        }
        let gaps = vec![gap(
            cause.gap_code(),
            &cause.gap_message(),
            "warning",
            true,
            Some("repository.semantic"),
            Some(path),
        )];
        Ok(SemanticResponse {
            data: json!({
                "operation": operation,
                "pending": false,
                "request_id": Value::Null,
                "cached": Value::Null,
                "locations": [],
                "hover": Value::Null,
                "call_hierarchy": [],
                "server_id": Value::Null,
                "document_version": Value::Null,
                "soft_deadline_ms": 800,
            }),
            cache_status: "not_attached".to_string(),
            coverage: coverage(
                false,
                vec![path.to_string()],
                Vec::new(),
                Vec::new(),
                Vec::new(),
                gaps,
                None,
            ),
            warnings: vec![cause.warning("Semantic navigation")],
        })
    }

    fn unavailable_inspection(
        &self,
        operation: &str,
        semantic_mode: &str,
        path: &str,
        cause: SemanticUnavailability<'_>,
    ) -> Result<SemanticResponse> {
        if semantic_mode == "semantic_required" {
            bail!("{}", cause.required_message());
        }
        let gaps = vec![gap(
            cause.gap_code(),
            &cause.gap_message(),
            "warning",
            true,
            Some("repository.semantic"),
            Some(path),
        )];
        Ok(SemanticResponse {
            data: json!({
                "operation": operation,
                "pending": false,
                "request_id": Value::Null,
                "cached": Value::Null,
                "items": [],
                "server_id": Value::Null,
                "document_version": Value::Null,
                "soft_deadline_ms": 800,
            }),
            cache_status: "not_attached".to_string(),
            coverage: coverage(
                false,
                vec![path.to_string()],
                Vec::new(),
                Vec::new(),
                Vec::new(),
                gaps,
                None,
            ),
            warnings: vec![cause.warning("Semantic inspection")],
        })
    }
}

#[derive(Debug)]
struct SemanticTarget {
    path: String,
    line_zero: u64,
    column_zero: u64,
}

#[derive(Debug, Clone, Copy)]
enum SemanticUnavailability<'a> {
    NoNativeServer,
    MissingCapability {
        server_id: &'a str,
        capability: &'a str,
    },
}

impl SemanticUnavailability<'_> {
    fn gap_code(&self) -> &'static str {
        match self {
            Self::NoNativeServer => "native_lsp_unavailable",
            Self::MissingCapability { .. } => "native_lsp_capability_unavailable",
        }
    }

    fn gap_message(&self) -> String {
        match self {
            Self::NoNativeServer => {
                "No applicable native LSP completed its capability probe.".to_string()
            }
            Self::MissingCapability {
                server_id,
                capability,
            } => format!("Native LSP {server_id} did not advertise {capability}."),
        }
    }

    fn required_message(&self) -> String {
        match self {
            Self::NoNativeServer => {
                "semantic_required requested but no native LSP completed its capability probe"
                    .to_string()
            }
            Self::MissingCapability {
                server_id,
                capability,
            } => format!(
                "semantic_required requested but native LSP {server_id} did not advertise {capability}"
            ),
        }
    }

    fn warning(&self, subject: &str) -> String {
        match self {
            Self::NoNativeServer => {
                format!("{subject} is unavailable; no non-native fallback was used.")
            }
            Self::MissingCapability {
                server_id,
                capability,
            } => format!(
                "{subject} is unavailable; native LSP {server_id} did not advertise {capability} and no non-native fallback was used."
            ),
        }
    }
}

fn normalize_navigation(
    operation: &str,
    result: LspQueryResult,
    root: &Path,
    default_server: Option<&str>,
    document_version: Option<i64>,
) -> Result<Value> {
    match result {
        LspQueryResult::Ready {
            request_id,
            value,
            server_id,
            ..
        } => {
            let (locations, hover, call_hierarchy) = match operation {
                "hover" => (Vec::new(), value, Vec::new()),
                "call_hierarchy" | "incoming_calls" | "outgoing_calls" => (
                    Vec::new(),
                    Value::Null,
                    value.as_array().cloned().unwrap_or_default(),
                ),
                _ => (normalize_locations(&value, root)?, Value::Null, Vec::new()),
            };
            Ok(json!({
                "operation": operation,
                "pending": false,
                "request_id": request_id.to_string(),
                "cached": Value::Null,
                "locations": locations,
                "hover": hover,
                "call_hierarchy": call_hierarchy,
                "server_id": server_id,
                "document_version": document_version,
                "soft_deadline_ms": 800,
            }))
        }
        LspQueryResult::Pending {
            request_id,
            cached,
            server_id,
            soft_deadline_ms,
            ..
        } => Ok(json!({
            "operation": operation,
            "pending": true,
            "request_id": request_id.to_string(),
            "cached": cached,
            "locations": [],
            "hover": Value::Null,
            "call_hierarchy": [],
            "server_id": server_id,
            "document_version": document_version,
            "soft_deadline_ms": soft_deadline_ms,
        })),
    }
    .map(|mut value| {
        if value.get("server_id").is_none()
            && let Some(server) = default_server
            && let Some(object) = value.as_object_mut()
        {
            object.insert("server_id".to_string(), Value::String(server.to_string()));
        }
        value
    })
}

fn normalize_inspection(
    operation: &str,
    result: LspQueryResult,
    default_server: Option<&str>,
    document_version: Option<i64>,
) -> Value {
    match result {
        LspQueryResult::Ready {
            request_id,
            value,
            server_id,
            ..
        } => json!({
            "operation": operation,
            "pending": false,
            "request_id": request_id.to_string(),
            "cached": Value::Null,
            "items": inspection_items(operation, value),
            "server_id": server_id,
            "document_version": document_version,
            "soft_deadline_ms": 800,
        }),
        LspQueryResult::Pending {
            request_id,
            cached,
            server_id,
            soft_deadline_ms,
            ..
        } => json!({
            "operation": operation,
            "pending": true,
            "request_id": request_id.to_string(),
            "cached": cached,
            "items": [],
            "server_id": server_id,
            "document_version": document_version,
            "soft_deadline_ms": soft_deadline_ms,
        }),
    }
    .as_object()
    .cloned()
    .map(Value::Object)
    .unwrap_or_else(|| {
        json!({
            "operation":operation,
            "pending":false,
            "request_id":Value::Null,
            "cached":Value::Null,
            "items":[],
            "server_id":default_server,
            "document_version":document_version,
            "soft_deadline_ms":800,
        })
    })
}

fn inspection_items(operation: &str, value: Value) -> Vec<Value> {
    match operation {
        "diagnostics" => value
            .get("items")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default(),
        "completion" => value
            .get("items")
            .and_then(Value::as_array)
            .cloned()
            .or_else(|| value.as_array().cloned())
            .unwrap_or_default(),
        "signature_help" => {
            if value.is_null() {
                Vec::new()
            } else {
                vec![value]
            }
        }
        "code_actions" => value.as_array().cloned().unwrap_or_default(),
        _ => Vec::new(),
    }
}

fn normalize_locations(value: &Value, root: &Path) -> Result<Vec<Value>> {
    let values = if let Some(array) = value.as_array() {
        array.clone()
    } else if value.is_object() {
        vec![value.clone()]
    } else {
        Vec::new()
    };
    values
        .into_iter()
        .filter_map(|location| normalize_location(&location, root).transpose())
        .collect()
}

fn normalize_location(value: &Value, root: &Path) -> Result<Option<Value>> {
    let uri = value
        .get("uri")
        .or_else(|| value.get("targetUri"))
        .and_then(Value::as_str);
    let Some(uri) = uri else { return Ok(None) };
    let range = value
        .get("range")
        .or_else(|| value.get("targetSelectionRange"))
        .or_else(|| value.get("targetRange"))
        .context("LSP location omitted range")?;
    let url = Url::parse(uri).context("invalid LSP location URI")?;
    let path = url
        .to_file_path()
        .map_err(|_| anyhow::anyhow!("LSP location was not a file URI"))?;
    let canonical = fs::canonicalize(&path)
        .with_context(|| format!("resolving LSP location {}", path.display()))?;
    if !canonical.starts_with(root) {
        return Ok(None);
    }
    let relative = canonical
        .strip_prefix(root)
        .unwrap_or(&canonical)
        .to_string_lossy()
        .replace('\\', "/");
    let start = range
        .get("start")
        .and_then(Value::as_object)
        .context("location range omitted start")?;
    let end = range.get("end").and_then(Value::as_object);
    Ok(Some(json!({
        "path": relative,
        "line": start.get("line").and_then(Value::as_u64).unwrap_or(0) + 1,
        "column": start.get("character").and_then(Value::as_u64).unwrap_or(0) + 1,
        "end_line": end.and_then(|value| value.get("line")).and_then(Value::as_u64).map(|value| value + 1),
        "end_column": end.and_then(|value| value.get("character")).and_then(Value::as_u64).map(|value| value + 1),
        "uri": uri,
    })))
}

fn language_key(language: &str) -> &str {
    match language {
        "typescript" | "tsx" | "javascript" | "jsx" => "typescript",
        "python" => "python",
        "bash" => "bash",
        other => other,
    }
}

fn required_string<'a>(arguments: &'a Value, name: &str) -> Result<&'a str> {
    arguments
        .get(name)
        .and_then(Value::as_str)
        .with_context(|| format!("missing string argument: {name}"))
}

fn required_u64(arguments: &Value, name: &str) -> Result<u64> {
    arguments
        .get(name)
        .and_then(Value::as_u64)
        .with_context(|| format!("missing integer argument: {name}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use soleaux_intelligence::{index::IndexConfig, lsp::LspServerSpec};
    use soleaux_storage::Store;
    use tempfile::{TempDir, tempdir};

    const STUB_LANGUAGE_SERVER: &str = r#"import json
import sys


def read_message(stream):
    length = None
    while True:
        line = stream.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":", 1)[1].strip())
    if length is None:
        return None
    return json.loads(stream.read(length))


def write_message(stream, payload):
    body = json.dumps(payload).encode("utf-8")
    stream.write(b"Content-Length: %d\r\n\r\n%s" % (len(body), body))
    stream.flush()


capabilities = json.loads(sys.argv[1])
while True:
    message = read_message(sys.stdin.buffer)
    if message is None:
        break
    identifier = message.get("id")
    if identifier is None:
        continue
    result = {"capabilities": capabilities} if message.get("method") == "initialize" else None
    write_message(sys.stdout.buffer, {"jsonrpc": "2.0", "id": identifier, "result": result})
"#;

    async fn service_with_capabilities(capabilities: Value) -> (TempDir, SemanticService) {
        let temp = tempdir().expect("tempdir");
        let root = fs::canonicalize(temp.path()).expect("canonical root");
        fs::create_dir_all(root.join("src")).expect("src");
        fs::write(
            root.join("src/context.ts"),
            "export function compileContext(task: string) {\n  return task;\n}\n",
        )
        .expect("fixture");
        let stub = root.join("stub_language_server.py");
        fs::write(&stub, STUB_LANGUAGE_SERVER).expect("stub");
        let store = Store::open(root.join("index.sqlite3")).expect("store");
        let index = RepositoryIndex::open(&root, store, IndexConfig::default()).expect("index");
        index.refresh().await.expect("refresh");
        let lsp = LspSupervisor::new(8 * 1024 * 1024);
        let probe = lsp
            .ensure_server(LspServerSpec {
                server_id: "typescript".to_string(),
                command: "python3".to_string(),
                arguments: vec![stub.to_string_lossy().to_string(), capabilities.to_string()],
                root_uri: Url::from_directory_path(&root)
                    .expect("root uri")
                    .to_string(),
                initialization_options: Value::Null,
                workspace_folders: Vec::new(),
                hard_timeout_ms: 10_000,
                idle_timeout_ms: 60_000,
                rss_limit_bytes: 512 * 1024 * 1024,
                maximum_open_documents: 16,
            })
            .await
            .expect("stub language server");
        let service = SemanticService::new(
            index,
            lsp,
            Arc::new(RwLock::new(HashMap::from([(
                "typescript".to_string(),
                "typescript".to_string(),
            )]))),
            Arc::new(RwLock::new(vec![probe])),
        );
        (temp, service)
    }

    #[tokio::test]
    async fn inspect_degrades_when_the_server_omits_pull_diagnostics() {
        let (_temp, service) = service_with_capabilities(
            json!({"completionProvider":{"triggerCharacters":["."]},"hoverProvider":true}),
        )
        .await;

        let degraded = service
            .inspect(&json!({
                "operation":"diagnostics",
                "path":"src/context.ts",
                "line":1,
                "column":1,
            }))
            .await
            .expect("missing capability degrades instead of failing");
        assert_eq!(degraded.cache_status, "not_attached");
        assert_eq!(degraded.coverage.get("complete"), Some(&json!(false)));
        assert_eq!(
            degraded
                .coverage
                .pointer("/gaps/0/code")
                .and_then(Value::as_str),
            Some("native_lsp_capability_unavailable")
        );
        assert_eq!(
            degraded
                .coverage
                .pointer("/gaps/0/message")
                .and_then(Value::as_str),
            Some("Native LSP typescript did not advertise diagnosticProvider.")
        );
        assert_eq!(
            degraded.warnings,
            vec![
                "Semantic inspection is unavailable; native LSP typescript did not advertise diagnosticProvider and no non-native fallback was used."
                    .to_string()
            ]
        );
        assert_eq!(degraded.data.get("items"), Some(&json!([])));
        assert_eq!(degraded.data.get("server_id"), Some(&Value::Null));

        let advertised = service
            .inspect(&json!({
                "operation":"completion",
                "path":"src/context.ts",
                "line":2,
                "column":10,
            }))
            .await
            .expect("advertised capability still reaches the server");
        assert!(advertised.warnings.is_empty());
        assert_eq!(advertised.coverage.get("complete"), Some(&json!(true)));
    }

    #[tokio::test]
    async fn semantic_required_inspection_reports_the_missing_capability() {
        let (_temp, service) = service_with_capabilities(json!({"hoverProvider":true})).await;

        let error = service
            .inspect(&json!({
                "operation":"diagnostics",
                "path":"src/context.ts",
                "line":1,
                "column":1,
                "semantic_mode":"semantic_required",
            }))
            .await
            .expect_err("semantic_required must not degrade");
        assert_eq!(
            error.to_string(),
            "semantic_required requested but native LSP typescript did not advertise diagnosticProvider"
        );
    }

    #[tokio::test]
    async fn navigation_degrades_when_the_server_omits_the_requested_capability() {
        let (_temp, service) = service_with_capabilities(json!({"definitionProvider":true})).await;

        let degraded = service
            .navigate(&json!({
                "operation":"implementation",
                "path":"src/context.ts",
                "line":1,
                "column":17,
            }))
            .await
            .expect("missing capability degrades instead of failing");
        assert_eq!(degraded.cache_status, "not_attached");
        assert_eq!(degraded.coverage.get("complete"), Some(&json!(false)));
        assert_eq!(
            degraded.warnings,
            vec![
                "Semantic navigation is unavailable; native LSP typescript did not advertise implementationProvider and no non-native fallback was used."
                    .to_string()
            ]
        );
        assert_eq!(degraded.data.get("locations"), Some(&json!([])));
    }
}
