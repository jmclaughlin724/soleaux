#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARTS = sorted((ROOT / ".ci").glob("p4-017-schema.part-*"))
EXPECTED_B64_BYTES = 30748
EXPECTED_B64_SHA256 = "eaca8a76c2fb32ab65d89478849bb8c15d97b21479648a829857410f9ad94770"
EXPECTED_SOURCE_BYTES = 23059
EXPECTED_SOURCE_SHA256 = "411caa8e3d8f347d30a6b8d9d2ed1a06826b12a49be6d239671c416e2d97fb70"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


if len(PARTS) != 9:
    raise SystemExit(f"expected 9 schema carrier parts, found {len(PARTS)}")
encoded = b"".join(
    part.read_bytes().replace(b"\n", b"").replace(b"\r", b"") for part in PARTS
)
if len(encoded) != EXPECTED_B64_BYTES:
    raise SystemExit(f"unexpected base64 size: {len(encoded)}")
if hashlib.sha256(encoded).hexdigest() != EXPECTED_B64_SHA256:
    raise SystemExit("schema base64 digest mismatch")
source = base64.b64decode(encoded, validate=True)
if len(source) != EXPECTED_SOURCE_BYTES:
    raise SystemExit(f"unexpected schema source size: {len(source)}")
if hashlib.sha256(source).hexdigest() != EXPECTED_SOURCE_SHA256:
    raise SystemExit("schema source digest mismatch")

schema_path = ROOT / "native/daemon/mcp/src/schema.rs"
schema_path.write_bytes(source)

lib_path = ROOT / "native/daemon/mcp/src/lib.rs"
text = lib_path.read_text(encoding="utf-8")
text = replace_once(
    text,
    "pub mod registry;\npub mod semantic;\n",
    "pub mod registry;\npub mod semantic;\nmod schema;\n",
    "schema module declaration",
)
text = replace_once(
    text,
    '''    fn is_public_tool(&self, name: &str) -> bool {
        self.active_tools.iter().any(|active| active == name)
    }

    pub async fn call_async(&self, name: &str, arguments: &Value) -> Result<ToolEnvelopeV2> {
        if !self.is_public_tool(name) {
            bail!("tool is not active in the binding Soleaux public profile: {name}");
        }
''',
    '''    fn is_public_tool(&self, name: &str) -> bool {
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
''',
    "tool argument validation method",
)
text = replace_once(
    text,
    '''                let arguments = request
                    .pointer("/params/arguments")
                    .cloned()
                    .unwrap_or_else(|| json!({}));
                let started = Instant::now();
''',
    '''                let arguments = request
                    .pointer("/params/arguments")
                    .cloned()
                    .unwrap_or_else(|| json!({}));
                if let Err(error) = self.validate_tool_arguments(name, &arguments) {
                    return Some(json_rpc_error(id, -32602, error.to_string()));
                }
                let started = Instant::now();
''',
    "JSON-RPC invalid params handling",
)
text = replace_once(
    text,
    '''    use super::*;
    use std::fs;
    use tempfile::tempdir;

    #[tokio::test]
    async fn canonical_profile_is_exactly_twelve_in_locked_order() {
''',
    '''    use super::*;
    use std::fs;
    use tempfile::tempdir;

    #[test]
    fn locked_tool_input_schemas_are_supported_and_closed() {
        for definition in all_tool_definitions().values() {
            schema::validate_schema_definition(&definition.input_schema).unwrap_or_else(
                |error| panic!("{} has unsupported input schema: {error}", definition.name),
            );
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
                    .is_some_and(|message| message.contains("invalid arguments for context.compile"))
            );
            assert!(response.get("result").is_none());
        }
    }

    #[tokio::test]
    async fn canonical_profile_is_exactly_twelve_in_locked_order() {
''',
    "schema validation regression tests",
)
lib_path.write_text(text, encoding="utf-8")
