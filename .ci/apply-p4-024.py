from pathlib import Path


def lines(*values: str) -> str:
    return "\n".join(values) + "\n"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if text.count(old) != 1:
        raise SystemExit(f"P4-024 target drifted: {label}")
    return text.replace(old, new, 1)


intelligence = Path("native/daemon/intelligence/src/lib.rs")
text = intelligence.read_text(encoding="utf-8")
text = replace_once(
    text,
    lines(
        "    pub relations: Vec<String>,",
        "    pub statement_count: usize,",
        "    pub engine: String,",
    ),
    lines(
        "    pub relations: Vec<String>,",
        "    pub statement_count: usize,",
        "    pub errors: Vec<String>,",
        "    pub engine: String,",
    ),
    "PostgresAnalysis fields",
)
text = replace_once(
    text,
    lines(
        "pub fn analyze_postgres_sql(source: &str) -> Result<PostgresAnalysis> {",
        "    let parsed = pg_query::parse(source).context(\"PostgreSQL parser rejected the statement\")?;",
        "    let normalized = pg_query::normalize(source).context(\"normalizing PostgreSQL SQL\")?;",
    ),
    lines(
        "pub fn analyze_postgres_sql(source: &str) -> Result<PostgresAnalysis> {",
        "    let parsed = match pg_query::parse(source) {",
        "        Ok(parsed) => parsed,",
        "        Err(error) => {",
        "            return Ok(PostgresAnalysis {",
        "                valid: false,",
        "                normalized: String::new(),",
        "                fingerprint: String::new(),",
        "                relations: Vec::new(),",
        "                statement_count: 0,",
        "                errors: vec![error.to_string()],",
        "                engine: \"pg_query/libpg_query\".into(),",
        "                engine_version: PG_QUERY_ENGINE_VERSION.into(),",
        "            });",
        "        }",
        "    };",
        "    let normalized = pg_query::normalize(source).context(\"normalizing PostgreSQL SQL\")?;",
    ),
    "parse outcome",
)
text = replace_once(
    text,
    lines(
        "        relations,",
        "        statement_count: parsed.protobuf.stmts.len(),",
        "        engine: \"pg_query/libpg_query\".into(),",
    ),
    lines(
        "        relations,",
        "        statement_count: parsed.protobuf.stmts.len(),",
        "        errors: Vec::new(),",
        "        engine: \"pg_query/libpg_query\".into(),",
    ),
    "valid result",
)
text = replace_once(
    text,
    lines(
        "    #[test]",
        "    fn postgres_analysis_uses_native_postgres_parser() {",
        "        let result = analyze_postgres_sql(\"select * from public.users where id = 42\").unwrap();",
        "        assert!(result.valid);",
        "        assert!(result.relations.iter().any(|name| name.contains(\"users\")));",
        "        assert!(!result.fingerprint.is_empty());",
        "    }",
    ),
    lines(
        "    #[test]",
        "    fn postgres_analysis_uses_native_postgres_parser() {",
        "        let result = analyze_postgres_sql(\"select * from public.users where id = 42\").unwrap();",
        "        assert!(result.valid);",
        "        assert!(result.relations.iter().any(|name| name.contains(\"users\")));",
        "        assert!(!result.fingerprint.is_empty());",
        "        assert!(result.errors.is_empty());",
        "    }",
        "",
        "    #[test]",
        "    fn postgres_analysis_returns_invalid_sql_as_typed_data() {",
        "        let result = analyze_postgres_sql(\"select from where\").unwrap();",
        "        assert!(!result.valid);",
        "        assert!(result.normalized.is_empty());",
        "        assert!(result.fingerprint.is_empty());",
        "        assert!(result.relations.is_empty());",
        "        assert_eq!(result.statement_count, 0);",
        "        assert!(!result.errors.is_empty());",
        "    }",
    ),
    "intelligence tests",
)
intelligence.write_text(text, encoding="utf-8")

mcp = Path("native/daemon/mcp/src/lib.rs")
text = mcp.read_text(encoding="utf-8")
text = replace_once(
    text,
    "            \"errors\": [],\n",
    "            \"errors\": analysis.errors,\n",
    "MCP errors",
)
text = replace_once(
    text,
    "        metadata.trust = \"verified_sql_structure\".to_string();\n",
    lines(
        "        metadata.trust = if analysis.valid {",
        "            \"verified_sql_structure\"",
        "        } else {",
        "            \"verified_validation_result\"",
        "        }",
        "        .to_string();",
    ),
    "MCP trust",
)
marker = lines(
    "    #[tokio::test]",
    "    async fn context_compile_returns_v2_typed_packet() {",
)
regression = lines(
    "    #[tokio::test]",
    "    async fn invalid_postgres_sql_returns_a_successful_typed_validation_result() {",
    "        let temp = tempdir().expect(\"tempdir\");",
    "        fs::write(",
    "            temp.path().join(\"Cargo.toml\"),",
    "            \"[package]\\nname='fixture'\\nversion='0.1.0'\\n\",",
    "        )",
    "        .expect(\"fixture\");",
    "        let server = PublicMcpServer::with_store(temp.path(), temp.path().join(\"index.sqlite3\"))",
    "            .expect(\"server\")",
    "            .substitute_tool(\"restart_lsp\", OPTIONAL_POSTGRES)",
    "            .expect(\"substitution\");",
    "        server.prepare().await.expect(\"prepare\");",
    "        let envelope = server",
    "            .call_async(OPTIONAL_POSTGRES, &json!({\"sql\":\"select from where\"}))",
    "            .await",
    "            .expect(\"typed validation result\");",
    "        assert_eq!(envelope.status, \"ok\");",
    "        assert_eq!(envelope.data.get(\"valid\").and_then(Value::as_bool), Some(false));",
    "        assert_eq!(",
    "            envelope.data.get(\"statement_count\").and_then(Value::as_u64),",
    "            Some(0)",
    "        );",
    "        assert!(",
    "            envelope",
    "                .data",
    "                .get(\"errors\")",
    "                .and_then(Value::as_array)",
    "                .is_some_and(|errors| !errors.is_empty())",
    "        );",
    "        assert_eq!(envelope.trust, \"verified_validation_result\");",
    "    }",
    "",
)
text = replace_once(text, marker, regression + marker, "MCP regression")
mcp.write_text(text, encoding="utf-8")
