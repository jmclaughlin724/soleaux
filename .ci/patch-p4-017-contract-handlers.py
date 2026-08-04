#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match in {path}, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


lib = ROOT / "native/daemon/mcp/src/lib.rs"
replace_once(
    lib,
    '''    async fn call_preview(&self, arguments: &Value, started: Instant) -> Result<ToolEnvelopeV2> {
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
''',
    '''    async fn call_preview(&self, arguments: &Value, started: Instant) -> Result<ToolEnvelopeV2> {
        let preview = self.editor.structural_preview(arguments)?;
''',
    "locked structural preview dispatch",
)
replace_once(
    lib,
    '''    async fn call_edit(&self, arguments: &Value, started: Instant) -> Result<ToolEnvelopeV2> {
        let preview_id = required_string(arguments, "preview_id")?;
        let digest = required_string(arguments, "digest")?;
        let confirm = arguments
            .get("confirm")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        let data = self.editor.apply(preview_id, digest, confirm).await?;
''',
    '''    async fn call_edit(&self, arguments: &Value, started: Instant) -> Result<ToolEnvelopeV2> {
        let preview_id = required_string(arguments, "preview_id")?;
        let preimage_sha256 = required_string(arguments, "preimage_sha256")?;
        let data = self
            .editor
            .apply_preimage(preview_id, preimage_sha256)
            .await?;
''',
    "locked edit dispatch",
)

editor = ROOT / "native/daemon/mcp/src/editor.rs"
replace_once(
    editor,
    '''    pub async fn apply(&self, preview_id: &str, digest: &str, confirm: bool) -> Result<Value> {
''',
    '''    pub async fn apply_preimage(
        &self,
        preview_id: &str,
        preimage_sha256: &str,
    ) -> Result<Value> {
        let preview = self.load(preview_id)?;
        if preview.patches.len() != 1 {
            bail!("the locked edit contract accepts exactly one preview preimage");
        }
        if preview.patches[0].preimage_sha256 != preimage_sha256 {
            bail!("preview preimage SHA-256 does not match");
        }
        self.apply(preview_id, &preview.digest, true).await
    }

    pub async fn apply(&self, preview_id: &str, digest: &str, confirm: bool) -> Result<Value> {
''',
    "locked preimage edit adapter",
)

semantic = ROOT / "native/daemon/mcp/src/semantic.rs"
replace_once(
    semantic,
    '''        let operation = required_string(arguments, "operation")?;
        let target = self.resolve_target(arguments)?;
''',
    '''        let operation = required_string(arguments, "operation")?;
        let normalized = normalize_position_arguments(arguments)?;
        let target = self.resolve_target(&normalized)?;
''',
    "navigate canonical position normalization",
)
replace_once(
    semantic,
    '''        let operation = required_string(arguments, "operation")?;
        let path = required_string(arguments, "path")?;
        let line = required_u64(arguments, "line")?;
        let column = required_u64(arguments, "column")?;
''',
    '''        let operation = required_string(arguments, "operation")?;
        let normalized = normalize_position_arguments(arguments)?;
        let path = required_string(&normalized, "path")?;
        let line = required_u64(&normalized, "line")?;
        let column = required_u64(&normalized, "column")?;
''',
    "inspect canonical position normalization",
)
replace_once(
    semantic,
    '''fn required_string<'a>(arguments: &'a Value, name: &str) -> Result<&'a str> {
''',
    '''fn normalize_position_arguments(arguments: &Value) -> Result<Value> {
    let Some(position) = arguments.get("position") else {
        return Ok(arguments.clone());
    };
    let position = position
        .as_object()
        .context("position must be an object")?;
    let line = position
        .get("line")
        .and_then(Value::as_u64)
        .context("position.line must be an integer")?;
    let column = position
        .get("column")
        .and_then(Value::as_u64)
        .context("position.column must be an integer")?;
    let mut normalized = arguments
        .as_object()
        .cloned()
        .context("semantic arguments must be an object")?;
    normalized.insert("line".to_string(), json!(line.saturating_add(1)));
    normalized.insert("column".to_string(), json!(column.saturating_add(1)));
    Ok(Value::Object(normalized))
}

fn required_string<'a>(arguments: &'a Value, name: &str) -> Result<&'a str> {
''',
    "canonical position helper",
)

smoke = ROOT / "native/scripts/phase1_mcp_smoke.py"
replace_once(
    smoke,
    '''            {"operation": "definition", "path": "src/context.ts", "line": 1, "column": 17},
''',
    '''            {
                "language": "typescript",
                "operation": "definition",
                "path": "src/context.ts",
                "position": {"line": 0, "column": 16},
            },
''',
    "navigate smoke payload",
)
replace_once(
    smoke,
    '''            {"operation": "diagnostics", "path": "src/context.ts", "line": 1, "column": 1},
''',
    '''            {
                "language": "typescript",
                "operation": "diagnostics",
                "path": "src/context.ts",
                "position": {"line": 0, "column": 0},
            },
''',
    "inspect smoke payload",
)
replace_once(
    smoke,
    '''                "operation": "structural_rewrite",
                "paths": ["src/context.ts"],
''',
    '''                "paths": ["src/context.ts"],
''',
    "preview smoke payload",
)
replace_once(
    smoke,
    '''                "preview_id": preview["data"]["preview_id"],
                "digest": preview["data"]["digest"],
                "confirm": True,
''',
    '''                "preview_id": preview["data"]["preview_id"],
                "preimage_sha256": preview["data"]["patches"][0]["preimage_sha256"],
''',
    "edit smoke payload",
)
replace_once(
    smoke,
    '''        restart, is_error = mcp.tool("restart_lsp", {})
''',
    '''        restart, is_error = mcp.tool("restart_lsp", {"language": "typescript"})
''',
    "restart smoke payload",
)
