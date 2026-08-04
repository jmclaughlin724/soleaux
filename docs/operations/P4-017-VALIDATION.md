# P4-017 MCP Input-Schema Validation

**Task:** `P4-017`  
**Pull request:** #12  
**Status:** implemented and focused-gate validated; full repository CI required before merge  
**Version:** `0.4.0-dev.5`  
**Production claim:** unchanged (`false`)

## Defect closed

The MCP dispatcher previously passed raw `tools/call` arguments to handlers. Missing required fields, unknown fields, invalid top-level types, and range/cardinality violations could therefore reach handlers or be defaulted/clamped independently.

## Implementation

- Validate every call against the selected active tool definition's locked `inputSchema` before dispatch.
- Validate the locked schema definitions themselves; unsupported assertion keywords fail closed.
- Support the exact schema assertions currently used by the twelve canonical and optional tool definitions, including the two locked path/digest patterns.
- Return JSON-RPC `-32602` for invalid `tools/call` parameters.
- Return an error before dispatch for direct `call_async` use.
- Add no new dependency and change no locked contract bytes.

## Focused evidence

The P4-017 source applicator completed these gates before committing normal source:

```text
cargo fmt --all --check
cargo check -p soleaux-mcp --all-targets --all-features --locked
cargo clippy -p soleaux-mcp --all-targets --all-features --locked -- -D warnings
cargo test -p soleaux-mcp --all-features --locked schema
cargo test -p soleaux-mcp --all-features --locked invalid_tool_arguments
cargo test -p soleaux-mcp --all-features --locked json_rpc_invalid_tool_arguments
cargo test -p soleaux-mcp --all-features --locked locked_tool_input_schemas
cargo test -p soleaux-mcp --all-features --locked locked_patterns
```

Observed result: all focused format, compile, Clippy and regression gates passed. The initial schema-definition audit identified the locked safe-relative-path and lowercase-SHA-256 `pattern` assertions; exact support and negative tests were added rather than ignoring the keyword.

## Merge gate

PR #12 may merge only after the normal-source head passes the repository's full CI and documentation consistency workflows. The canonical task/status files advance to `P4-018` after that merge.
