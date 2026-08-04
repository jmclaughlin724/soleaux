#!/usr/bin/env python3
# ruff: noqa: E501
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "native/daemon/mcp/src/lib.rs"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


text = LIB.read_text(encoding="utf-8")
text = replace_once(
    text,
    "    sync::{Arc, OnceLock, RwLock},\n",
    "    sync::{Arc, OnceLock, RwLock},\n",
    "stable std sync import",
)
text = replace_once(
    text,
    "    sync::RwLock as AsyncRwLock,\n",
    "    sync::{Mutex as AsyncMutex, RwLock as AsyncRwLock},\n",
    "tokio refresh mutex import",
)
text = replace_once(
    text,
    "    editor: EditorService,\n",
    "    editor: EditorService,\n    repository_read_refresh: Arc<AsyncMutex<()>>,\n",
    "server refresh field",
)
text = replace_once(
    text,
    "            editor,\n        })\n",
    "            editor,\n            repository_read_refresh: Arc::new(AsyncMutex::new(())),\n        })\n",
    "server refresh initialization",
)
old_prepare = '''    pub async fn prepare(&self) -> Result<IndexReport> {
        validate_active_profile(&self.active_tools)?;
        let report = self.index.refresh().await?;
        *self
            .last_index_report
            .write()
            .expect("index report lock poisoned") = Some(report.clone());
        let languages = self.index.languages()?;
'''
new_prepare = '''    async fn refresh_repository_read_state(&self) -> Result<IndexReport> {
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
'''
text = replace_once(text, old_prepare, new_prepare, "repository read barrier")
text = replace_once(
    text,
    '''        *self.language_servers.write().await = routes;
        *self.lsp_probes.write().await = probes;
        *self.registry.write().expect("registry lock poisoned") =
            scan_registry(self.root(), &self.index)?;
        Ok(report)
''',
    '''        *self.language_servers.write().await = routes;
        *self.lsp_probes.write().await = probes;
        Ok(report)
''',
    "prepare duplicate registry scan",
)
needle = '''    pub async fn call_async(&self, name: &str, arguments: &Value) -> Result<ToolEnvelopeV2> {
'''
if needle not in text:
    raise SystemExit("call_async signature not found")
if "self.validate_tool_arguments(name, arguments)?;" in text:
    old_call = '''    pub async fn call_async(&self, name: &str, arguments: &Value) -> Result<ToolEnvelopeV2> {
        self.validate_tool_arguments(name, arguments)?;
        let started = Instant::now();
'''
    new_call = '''    pub async fn call_async(&self, name: &str, arguments: &Value) -> Result<ToolEnvelopeV2> {
        self.validate_tool_arguments(name, arguments)?;
        if requires_fresh_repository_state(name) {
            self.refresh_repository_read_state().await?;
        }
        let started = Instant::now();
'''
else:
    old_call = '''    pub async fn call_async(&self, name: &str, arguments: &Value) -> Result<ToolEnvelopeV2> {
        if !self.is_public_tool(name) {
            bail!("tool is not active in the binding Soleaux public profile: {name}");
        }
        let started = Instant::now();
'''
    new_call = '''    pub async fn call_async(&self, name: &str, arguments: &Value) -> Result<ToolEnvelopeV2> {
        if !self.is_public_tool(name) {
            bail!("tool is not active in the binding Soleaux public profile: {name}");
        }
        if requires_fresh_repository_state(name) {
            self.refresh_repository_read_state().await?;
        }
        let started = Instant::now();
'''
text = replace_once(text, old_call, new_call, "read barrier before dispatch")
text = replace_once(
    text,
    '''fn is_supported_rpc_method(method: &str) -> bool {
''',
    '''fn requires_fresh_repository_state(tool: &str) -> bool {
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
''',
    "fresh-read tool set",
)
text = replace_once(
    text,
    '''    #[tokio::test]
    async fn canonical_profile_is_exactly_twelve_in_locked_order() {
''',
    '''    #[tokio::test]
    async fn structural_reads_refresh_external_mutations_and_deletions() {
        let temp = tempdir().expect("tempdir");
        fs::create_dir_all(temp.path().join("src")).expect("src");
        let source_path = temp.path().join("src/state.ts");
        fs::write(
            &source_path,
            "export function oldState() { return 'old'; }",
        )
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

        fs::write(
            &source_path,
            "export function newState() { return 'new'; }",
        )
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
''',
    "stale-index regression tests",
)
LIB.write_text(text, encoding="utf-8")
