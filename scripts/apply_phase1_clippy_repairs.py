#!/usr/bin/env python3
"""Apply deterministic Phase 1 Rust quality repairs after the hash-bound overlay."""

from __future__ import annotations

import sys
from pathlib import Path


def replace_exact(path: Path, old: str, new: str, *, label: str) -> None:
    source = path.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one preimage, found {count}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: apply_phase1_clippy_repairs.py <phase1-source>")

    root = Path(sys.argv[1]).resolve()
    envelope = root / "daemon/mcp/src/envelope.rs"
    registry = root / "daemon/mcp/src/registry.rs"
    semantic = root / "daemon/mcp/src/semantic.rs"
    mcp = root / "daemon/mcp/src/lib.rs"
    for path in (envelope, registry, semantic, mcp):
        if not path.is_file():
            raise SystemExit(f"missing Phase 1 repair precondition: {path}")

    replace_exact(
        envelope,
        "impl ToolEnvelopeV2 {\n",
        r'''#[derive(Debug, Clone)]
pub struct ToolError {
    pub error_type: String,
    pub message: String,
    pub retryable: bool,
    pub details: Value,
}

#[derive(Debug, Clone, Copy, Default)]
pub struct EvidenceRange<'a> {
    pub path: Option<&'a str>,
    pub start_line: Option<u64>,
    pub end_line: Option<u64>,
    pub start_byte: Option<u64>,
    pub end_byte: Option<u64>,
}

impl ToolEnvelopeV2 {
''',
        label="envelope argument records",
    )
    replace_exact(
        envelope,
        r'''    pub fn error(
        workspace_id: Uuid,
        workspace: &str,
        source: impl Into<String>,
        error_type: impl Into<String>,
        message: impl Into<String>,
        retryable: bool,
        details: Value,
        duration_us: u64,
    ) -> Self {
''',
        r'''    pub fn error(
        workspace_id: Uuid,
        workspace: &str,
        source: impl Into<String>,
        error: ToolError,
        duration_us: u64,
    ) -> Self {
''',
        label="error envelope signature",
    )
    replace_exact(
        envelope,
        r'''            error: Some(json!({
                "error_type": error_type.into(),
                "message": message.into(),
                "retryable": retryable,
                "details": details,
            })),
''',
        r'''            error: Some(json!({
                "error_type": error.error_type,
                "message": error.message,
                "retryable": error.retryable,
                "details": error.details,
            })),
''',
        label="error envelope payload",
    )
    replace_exact(
        envelope,
        r'''pub fn evidence(
    evidence_id: impl Into<String>,
    kind: impl Into<String>,
    summary: impl Into<String>,
    trust: &str,
    provenance: Value,
    path: Option<&str>,
    start_line: Option<u64>,
    end_line: Option<u64>,
    start_byte: Option<u64>,
    end_byte: Option<u64>,
) -> Value {
''',
        r'''pub fn evidence(
    evidence_id: impl Into<String>,
    kind: impl Into<String>,
    summary: impl Into<String>,
    trust: &str,
    provenance: Value,
    range: EvidenceRange<'_>,
) -> Value {
''',
        label="evidence signature",
    )
    replace_exact(
        envelope,
        r'''        "path": path,
        "start_line": start_line,
        "end_line": end_line,
        "start_byte": start_byte,
        "end_byte": end_byte,
''',
        r'''        "path": range.path,
        "start_line": range.start_line,
        "end_line": range.end_line,
        "start_byte": range.start_byte,
        "end_byte": range.end_byte,
''',
        label="evidence range payload",
    )

    replace_exact(
        registry,
        r'''        if matches!(name.as_ref(), "next.config.js" | "next.config.mjs" | "next.config.cjs" | "next.config.ts") {
            if let Some(parent) = entry.path().parent() {
                next_roots.insert(
                    parent
                        .strip_prefix(root)
                        .unwrap_or(parent)
                        .to_string_lossy()
                        .replace('\\', "/"),
                );
            }
        }
''',
        r'''        if matches!(name.as_ref(), "next.config.js" | "next.config.mjs" | "next.config.cjs" | "next.config.ts")
            && let Some(parent) = entry.path().parent()
        {
            next_roots.insert(
                parent
                    .strip_prefix(root)
                    .unwrap_or(parent)
                    .to_string_lossy()
                    .replace('\\', "/"),
            );
        }
''',
        label="Next.js framework detection condition",
    )

    replace_exact(
        semantic,
        r'''        if let Some(provider) = provider {
            if probes.iter().any(|probe| probe.server_id == provider) {
                targets.insert(provider.to_string());
            }
        }
        if let Some(language) = language {
            if let Some(server_id) = self.language_servers.read().await.get(language).cloned() {
                targets.insert(server_id);
            }
        }
''',
        r'''        if let Some(provider) = provider
            && probes.iter().any(|probe| probe.server_id == provider)
        {
            targets.insert(provider.to_string());
        }
        if let Some(language) = language
            && let Some(server_id) = self.language_servers.read().await.get(language).cloned()
        {
            targets.insert(server_id);
        }
''',
        label="LSP restart target selection",
    )

    replace_exact(
        mcp,
        "use envelope::{SuccessMetadata, ToolEnvelopeV2, coverage, evidence, gap, provenance};\n",
        r'''use envelope::{
    EvidenceRange, SuccessMetadata, ToolEnvelopeV2, ToolError, coverage, evidence, gap,
    provenance,
};
''',
        label="MCP envelope imports",
    )
    replace_exact(
        mcp,
        'pub const OPTIONAL_NEXTJS: &str = "next.get_routes";\n\n',
        r'''pub const OPTIONAL_NEXTJS: &str = "next.get_routes";

type SearchMatchesResult = (Vec<Value>, Vec<String>, Vec<Value>, bool);
type SymbolsDataResult = (Value, Vec<Value>, bool, Vec<Value>, Vec<String>, bool);
type ResolvedResourceResult = (String, Option<String>, Option<String>, Option<String>);

''',
        label="MCP result aliases",
    )
    replace_exact(
        mcp,
        r'''        let id = request.get("id").cloned();
        if id.is_none() {
            return None;
        }
        let id = id.unwrap_or(Value::Null);
''',
        '        let id = request.get("id").cloned()?;\n',
        label="async JSON-RPC notification handling",
    )
    replace_exact(
        mcp,
        r'''                    Err(error) => ToolEnvelopeV2::error(
                        self.workspace_id(),
                        &self.root.to_string_lossy(),
                        name,
                        "tool_execution_error",
                        error.to_string(),
                        false,
                        json!({}),
                        elapsed_us(started),
                    ),
''',
        r'''                    Err(error) => ToolEnvelopeV2::error(
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
''',
        label="tool error envelope construction",
    )
    replace_exact(
        mcp,
        '    ) -> Result<(Vec<Value>, Vec<String>, Vec<Value>, bool)> {\n',
        '    ) -> Result<SearchMatchesResult> {\n',
        label="search result alias",
    )
    replace_exact(
        mcp,
        '    ) -> Result<(Value, Vec<Value>, bool, Vec<Value>, Vec<String>, bool)> {\n',
        '    ) -> Result<SymbolsDataResult> {\n',
        label="symbol result alias",
    )
    replace_exact(
        mcp,
        'fn resolved_resource(value: &Value) -> Result<(String, Option<String>, Option<String>, Option<String>)> {\n',
        'fn resolved_resource(value: &Value) -> Result<ResolvedResourceResult> {\n',
        label="resolved resource alias",
    )

    evidence_replacements = (
        (
            r'''                    Some(&patch.path),
                    None,
                    None,
                    Some(patch.start_byte as u64),
                    Some(patch.end_byte as u64),
''',
            r'''                    EvidenceRange {
                        path: Some(&patch.path),
                        start_byte: Some(patch.start_byte as u64),
                        end_byte: Some(patch.end_byte as u64),
                        ..EvidenceRange::default()
                    },
''',
            "preview evidence range",
        ),
        (
            r'''                    Some(&file.path),
                    Some(symbol.start_row + 1),
                    Some(symbol.end_row + 1),
                    Some(symbol.start_byte),
                    Some(symbol.end_byte),
''',
            r'''                    EvidenceRange {
                        path: Some(&file.path),
                        start_line: Some(symbol.start_row + 1),
                        end_line: Some(symbol.end_row + 1),
                        start_byte: Some(symbol.start_byte),
                        end_byte: Some(symbol.end_byte),
                    },
''',
            "symbol evidence range",
        ),
        (
            r'''            Some(&item.path),
            Some(item.start_line),
            Some(item.end_line),
            item.start_byte,
            item.end_byte,
''',
            r'''            EvidenceRange {
                path: Some(&item.path),
                start_line: Some(item.start_line),
                end_line: Some(item.end_line),
                start_byte: item.start_byte,
                end_byte: item.end_byte,
            },
''',
            "context evidence range",
        ),
        (
            r'''                item.get("path").and_then(Value::as_str),
                item.get("start_line").and_then(Value::as_u64),
                item.get("end_line").and_then(Value::as_u64),
                item.get("start_byte").and_then(Value::as_u64),
                item.get("end_byte").and_then(Value::as_u64),
''',
            r'''                EvidenceRange {
                    path: item.get("path").and_then(Value::as_str),
                    start_line: item.get("start_line").and_then(Value::as_u64),
                    end_line: item.get("end_line").and_then(Value::as_u64),
                    start_byte: item.get("start_byte").and_then(Value::as_u64),
                    end_byte: item.get("end_byte").and_then(Value::as_u64),
                },
''',
            "search evidence range",
        ),
    )
    for old, new, label in evidence_replacements:
        replace_exact(mcp, old, new, label=label)

    print("Phase 1 Clippy repairs applied")


if __name__ == "__main__":
    main()
