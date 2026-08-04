//! Native LSP-backed navigation, inspection, editor preview, and restart paths.

use crate::envelope::{coverage, gap};
use anyhow::{Context, Result, bail};
use serde_json::{Value, json};
use soleaux_intelligence::{
    index::RepositoryIndex,
    lsp::{LspProbe, LspQuery, LspQueryResult, LspSupervisor},
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
            return self.unavailable_navigation(operation, semantic_mode, &target.path);
        };
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
        let semantic_mode = arguments
            .get("semantic_mode")
            .and_then(Value::as_str)
            .unwrap_or("best_available");
        let Some(server_id) = self.server_for_path(path).await? else {
            return self.unavailable_inspection(operation, semantic_mode, path);
        };
        let (uri, file, _text) = self.open_target(&server_id, path).await?;
        let line_zero = line.saturating_sub(1);
        let column_zero = column.saturating_sub(1);
        let (method, params) = match operation {
            "diagnostics" => (
                "textDocument/diagnostic",
                json!({"textDocument":{"uri":uri},"identifier":"soleaux"}),
            ),
            "completion" => (
                "textDocument/completion",
                json!({"textDocument":{"uri":uri},"position":{"line":line_zero,"character":column_zero}}),
            ),
            "signature_help" => (
                "textDocument/signatureHelp",
                json!({"textDocument":{"uri":uri},"position":{"line":line_zero,"character":column_zero}}),
            ),
            "code_actions" => (
                "textDocument/codeAction",
                json!({
                    "textDocument":{"uri":uri},
                    "range":{
                        "start":{"line":line_zero,"character":column_zero},
                        "end":{"line":line_zero,"character":column_zero},
                    },
                    "context":{"diagnostics":[]},
                }),
            ),
            _ => bail!("unsupported inspect operation: {operation}"),
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
    ) -> Result<SemanticResponse> {
        if semantic_mode == "semantic_required" {
            bail!("semantic_required requested but no native LSP completed its capability probe");
        }
        let gaps = vec![gap(
            "native_lsp_unavailable",
            "No applicable native LSP completed its capability probe.",
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
            warnings: vec![
                "Semantic navigation is unavailable; no non-native fallback was used.".to_string(),
            ],
        })
    }

    fn unavailable_inspection(
        &self,
        operation: &str,
        semantic_mode: &str,
        path: &str,
    ) -> Result<SemanticResponse> {
        if semantic_mode == "semantic_required" {
            bail!("semantic_required requested but no native LSP completed its capability probe");
        }
        let gaps = vec![gap(
            "native_lsp_unavailable",
            "No applicable native LSP completed its capability probe.",
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
            warnings: vec![
                "Semantic inspection is unavailable; no non-native fallback was used.".to_string(),
            ],
        })
    }
}

#[derive(Debug)]
struct SemanticTarget {
    path: String,
    line_zero: u64,
    column_zero: u64,
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
