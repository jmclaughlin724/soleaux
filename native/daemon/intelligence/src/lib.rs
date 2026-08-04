//! Native repository intelligence for Soleaux.
//!
//! The crate intentionally separates fast structural analysis from user-facing
//! source rewrites. Oxc and Tree-sitter produce source ranges and structural
//! facts; safe edits are applied as hash-guarded source patches by the edit
//! service, then formatted and revalidated.

pub mod context;
pub mod context_v2;
pub mod governance;
pub mod index;
pub mod lsp;
pub mod nextjs;
pub mod turborepo;

use anyhow::{Context, Result, bail};
use blake3::Hasher;
use moka::future::Cache;
use oxc_allocator::Allocator;
use oxc_parser::Parser as OxcParser;
use oxc_span::SourceType;
use serde::{Deserialize, Serialize};
use std::{
    path::Path,
    sync::Arc,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use tree_sitter::{InputEdit, Language, Node, Parser as TreeSitterParser, Point, Tree};
use uuid::Uuid;

pub const OXC_ENGINE_VERSION: &str = "0.142.0";
pub const TREE_SITTER_ENGINE_VERSION: &str = "0.26.11";
pub const PG_QUERY_ENGINE_VERSION: &str = "6.1.1";

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct Diagnostic {
    pub message: String,
    pub severity: String,
    pub start_byte: Option<usize>,
    pub end_byte: Option<usize>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct StructuralRange {
    pub kind: String,
    pub name: Option<String>,
    pub start_byte: usize,
    pub end_byte: usize,
    pub start_row: usize,
    pub end_row: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct SourceReference {
    pub content_hash: String,
    pub byte_length: usize,
    pub inline_utf8: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct Provenance {
    pub provider: String,
    pub provider_version: String,
    pub workspace_id: Uuid,
    pub relative_path: String,
    pub generated_at_unix_ms: u128,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ParseEnvelope {
    pub workspace_id: Uuid,
    pub relative_path: String,
    pub language: String,
    pub source: SourceReference,
    pub content_hash: String,
    pub byte_length: usize,
    pub engine: String,
    pub engine_version: String,
    pub grammar_version: String,
    pub grammar_hash: String,
    pub config_fingerprint: String,
    pub generation: u64,
    pub parse_duration_us: u64,
    pub range_encoding: String,
    pub diagnostics: Vec<Diagnostic>,
    pub errors: Vec<Diagnostic>,
    pub structural_ranges: Vec<StructuralRange>,
    pub program: Option<serde_json::Value>,
    pub tree: Option<serde_json::Value>,
    pub sql_ast: Option<serde_json::Value>,
    pub bash_ast: Option<serde_json::Value>,
    pub query_capabilities: Vec<String>,
    pub provenance: Provenance,
    pub trust: String,
    pub cache_status: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PostgresAnalysis {
    pub valid: bool,
    pub normalized: String,
    pub fingerprint: String,
    pub relations: Vec<String>,
    pub statement_count: usize,
    pub engine: String,
    pub engine_version: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ShellCommand {
    pub source: String,
    pub start_byte: usize,
    pub end_byte: usize,
    pub start_row: usize,
    pub end_row: usize,
    pub has_redirect: bool,
    pub has_substitution: bool,
    pub has_pipeline: bool,
}

#[derive(Debug, Clone, Hash, PartialEq, Eq)]
pub struct ParseKey {
    pub workspace_id: Uuid,
    pub relative_path: String,
    pub file_size: u64,
    pub content_hash: String,
    pub engine_version: String,
    pub grammar_hash: String,
    pub config_fingerprint: String,
}

#[derive(Clone)]
pub struct ParseCache {
    results: Cache<ParseKey, Arc<ParseEnvelope>>,
}

impl ParseCache {
    pub fn new(maximum_weight_bytes: u64) -> Self {
        let results = Cache::builder()
            .max_capacity(maximum_weight_bytes)
            .support_invalidation_closures()
            .weigher(|key: &ParseKey, value: &Arc<ParseEnvelope>| {
                let weight = key.relative_path.len()
                    + key.content_hash.len()
                    + value.source.inline_utf8.as_ref().map_or(0, String::len)
                    + value.structural_ranges.len() * 96
                    + value.diagnostics.len() * 160;
                u32::try_from(weight).unwrap_or(u32::MAX)
            })
            .time_to_idle(Duration::from_secs(15 * 60))
            .build();
        Self { results }
    }

    pub async fn get(&self, key: &ParseKey) -> Option<Arc<ParseEnvelope>> {
        let mut value = self.results.get(key).await?;
        Arc::make_mut(&mut value).cache_status = "hit".into();
        Some(value)
    }

    pub async fn insert(&self, key: ParseKey, value: ParseEnvelope) {
        self.results.insert(key, Arc::new(value)).await;
    }

    pub async fn invalidate_path(&self, workspace_id: Uuid, relative_path: &str) {
        let path = relative_path.to_owned();
        let _ = self.results.invalidate_entries_if(move |key, _| {
            key.workspace_id == workspace_id && key.relative_path == path
        });
    }

    pub fn weighted_size(&self) -> u64 {
        self.results.weighted_size()
    }
    pub fn entry_count(&self) -> u64 {
        self.results.entry_count()
    }
}

fn content_hash(source: &[u8]) -> String {
    blake3::hash(source).to_hex().to_string()
}

fn extract_node_name(node: Node<'_>, source: &[u8]) -> Option<String> {
    let mut cursor = node.walk();
    if !cursor.goto_first_child() {
        return None;
    }
    loop {
        let child = cursor.node();
        if matches!(
            child.kind(),
            "identifier" | "property_identifier" | "type_identifier"
        ) {
            return child.utf8_text(source).ok().map(str::to_owned);
        }
        if !cursor.goto_next_sibling() {
            break;
        }
    }
    None
}

fn collect_named_structure(node: Node<'_>, source: &[u8], output: &mut Vec<StructuralRange>) {
    const INTERESTING: &[&str] = &[
        "function_declaration",
        "generator_function_declaration",
        "class_declaration",
        "method_definition",
        "variable_declarator",
        "interface_declaration",
        "type_alias_declaration",
        "enum_declaration",
        "namespace_declaration",
        "import_statement",
        "export_statement",
        "lexical_declaration",
        "function_definition",
        "class_definition",
        "decorated_definition",
    ];
    if node.is_named() && INTERESTING.contains(&node.kind()) {
        output.push(StructuralRange {
            kind: node.kind().into(),
            name: extract_node_name(node, source),
            start_byte: node.start_byte(),
            end_byte: node.end_byte(),
            start_row: node.start_position().row,
            end_row: node.end_position().row,
        });
    }
    let mut cursor = node.walk();
    if cursor.goto_first_child() {
        loop {
            collect_named_structure(cursor.node(), source, output);
            if !cursor.goto_next_sibling() {
                break;
            }
        }
    }
}

#[derive(Debug, Clone, Copy)]
pub enum Grammar {
    TypeScript,
    Tsx,
    Python,
    Bash,
}

impl Grammar {
    fn language(self) -> Language {
        match self {
            Self::TypeScript => tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into(),
            Self::Tsx => tree_sitter_typescript::LANGUAGE_TSX.into(),
            Self::Python => tree_sitter_python::LANGUAGE.into(),
            Self::Bash => tree_sitter_bash::LANGUAGE.into(),
        }
    }

    fn name(self) -> &'static str {
        match self {
            Self::TypeScript => "typescript",
            Self::Tsx => "tsx",
            Self::Python => "python",
            Self::Bash => "bash",
        }
    }

    fn version(self) -> &'static str {
        match self {
            Self::TypeScript | Self::Tsx => "tree-sitter-typescript@0.23.2",
            Self::Python => "tree-sitter-python@0.25.0",
            Self::Bash => "tree-sitter-bash@0.25.1",
        }
    }
}

pub fn parse_oxc(
    workspace_id: Uuid,
    relative_path: &str,
    source: &str,
    generation: u64,
    config_fingerprint: &str,
) -> Result<ParseEnvelope> {
    let started = Instant::now();
    let source_type = SourceType::from_path(Path::new(relative_path)).unwrap_or_default();
    if !(source_type.is_javascript() || source_type.is_typescript()) {
        bail!("Oxc does not support this file type");
    }
    let allocator = Allocator::default();
    let parsed = OxcParser::new(&allocator, source, source_type).parse();
    let mut diagnostics = parsed
        .diagnostics
        .into_iter()
        .map(|error| Diagnostic {
            message: error.to_string(),
            severity: "error".into(),
            start_byte: None,
            end_byte: None,
        })
        .collect::<Vec<_>>();
    if parsed.panicked {
        diagnostics.push(Diagnostic {
            message: "Oxc parser stopped after an unrecoverable syntax error".into(),
            severity: "error".into(),
            start_byte: None,
            end_byte: None,
        });
    }

    let grammar = if relative_path.ends_with(".tsx") || relative_path.ends_with(".jsx") {
        Grammar::Tsx
    } else {
        Grammar::TypeScript
    };
    let mut tree_parser = TreeSitterParser::new();
    tree_parser
        .set_language(&grammar.language())
        .context("loading TypeScript grammar")?;
    let tree = tree_parser
        .parse(source.as_bytes(), None)
        .context("Tree-sitter returned no tree")?;
    let mut structural_ranges = Vec::new();
    collect_named_structure(tree.root_node(), source.as_bytes(), &mut structural_ranges);
    structural_ranges.insert(
        0,
        StructuralRange {
            kind: "program".into(),
            name: None,
            start_byte: 0,
            end_byte: source.len(),
            start_row: 0,
            end_row: source.lines().count().saturating_sub(1),
        },
    );

    let hash = content_hash(source.as_bytes());
    Ok(ParseEnvelope {
        workspace_id,
        relative_path: relative_path.into(),
        language: if source_type.is_typescript() {
            "typescript".into()
        } else {
            "javascript".into()
        },
        source: SourceReference {
            content_hash: hash.clone(),
            byte_length: source.len(),
            inline_utf8: (source.len() <= 64 * 1024).then(|| source.to_owned()),
        },
        content_hash: hash,
        byte_length: source.len(),
        engine: "oxc".into(),
        engine_version: OXC_ENGINE_VERSION.into(),
        grammar_version: grammar.version().into(),
        grammar_hash: parser_fingerprint(&[grammar.version(), TREE_SITTER_ENGINE_VERSION]),
        config_fingerprint: config_fingerprint.into(),
        generation,
        parse_duration_us: started.elapsed().as_micros().try_into().unwrap_or(u64::MAX),
        range_encoding: "utf8-bytes-zero-based".into(),
        errors: diagnostics.clone(),
        diagnostics,
        structural_ranges,
        // The process boundary intentionally carries extracted structure, not a
        // purported lossless serialization of Oxc's arena-backed AST.
        program: Some(
            serde_json::json!({"kind":"Program","sourceType": if source_type.is_typescript() {"typescript"} else {"javascript"}}),
        ),
        tree: None,
        sql_ast: None,
        bash_ast: None,
        query_capabilities: vec![
            "symbols".into(),
            "imports".into(),
            "exports".into(),
            "source_ranges".into(),
        ],
        provenance: Provenance {
            provider: "oxc".into(),
            provider_version: OXC_ENGINE_VERSION.into(),
            workspace_id,
            relative_path: relative_path.into(),
            generated_at_unix_ms: unix_ms(),
        },
        trust: "verified_code_structure".into(),
        cache_status: "miss".into(),
    })
}

pub fn parse_tree_sitter(
    workspace_id: Uuid,
    relative_path: &str,
    source: &str,
    grammar: Grammar,
    generation: u64,
    config_fingerprint: &str,
) -> Result<ParseEnvelope> {
    let started = Instant::now();
    let mut parser = TreeSitterParser::new();
    parser
        .set_language(&grammar.language())
        .context("loading Tree-sitter grammar")?;
    let tree = parser
        .parse(source.as_bytes(), None)
        .context("Tree-sitter returned no tree")?;
    let mut structural_ranges = Vec::new();
    collect_named_structure(tree.root_node(), source.as_bytes(), &mut structural_ranges);
    let diagnostics = if tree.root_node().has_error() {
        vec![Diagnostic {
            message: "Tree-sitter recovered from one or more syntax errors".into(),
            severity: "error".into(),
            start_byte: None,
            end_byte: None,
        }]
    } else {
        Vec::new()
    };
    let hash = content_hash(source.as_bytes());
    Ok(ParseEnvelope {
        workspace_id,
        relative_path: relative_path.into(),
        language: grammar.name().into(),
        source: SourceReference {
            content_hash: hash.clone(),
            byte_length: source.len(),
            inline_utf8: (source.len() <= 64 * 1024).then(|| source.to_owned()),
        },
        content_hash: hash,
        byte_length: source.len(),
        engine: "tree-sitter".into(),
        engine_version: TREE_SITTER_ENGINE_VERSION.into(),
        grammar_version: grammar.version().into(),
        grammar_hash: parser_fingerprint(&[grammar.version(), TREE_SITTER_ENGINE_VERSION]),
        config_fingerprint: config_fingerprint.into(),
        generation,
        parse_duration_us: started.elapsed().as_micros().try_into().unwrap_or(u64::MAX),
        range_encoding: "utf8-bytes-zero-based".into(),
        diagnostics: diagnostics.clone(),
        errors: diagnostics,
        structural_ranges,
        program: None,
        tree: Some(
            serde_json::json!({"rootKind": tree.root_node().kind(), "hasError": tree.root_node().has_error()}),
        ),
        sql_ast: None,
        bash_ast: None,
        query_capabilities: vec![
            "syntax_query".into(),
            "incremental_reparse".into(),
            "source_ranges".into(),
        ],
        provenance: Provenance {
            provider: "tree-sitter".into(),
            provider_version: TREE_SITTER_ENGINE_VERSION.into(),
            workspace_id,
            relative_path: relative_path.into(),
            generated_at_unix_ms: unix_ms(),
        },
        trust: "verified_code_structure".into(),
        cache_status: "miss".into(),
    })
}

pub struct IncrementalTree {
    parser: TreeSitterParser,
    grammar: Grammar,
    tree: Tree,
    source: Vec<u8>,
    generation: u64,
}

impl IncrementalTree {
    pub fn parse(grammar: Grammar, source: &[u8]) -> Result<Self> {
        let mut parser = TreeSitterParser::new();
        parser
            .set_language(&grammar.language())
            .context("loading Tree-sitter grammar")?;
        let tree = parser
            .parse(source, None)
            .context("Tree-sitter returned no tree")?;
        Ok(Self {
            parser,
            grammar,
            tree,
            source: source.to_vec(),
            generation: 1,
        })
    }

    pub fn apply_edit(
        &mut self,
        edit: InputEdit,
        replacement: &[u8],
    ) -> Result<Vec<StructuralRange>> {
        if edit.start_byte > edit.old_end_byte || edit.old_end_byte > self.source.len() {
            bail!("invalid incremental edit range");
        }
        self.tree.edit(&edit);
        self.source.splice(
            edit.start_byte..edit.old_end_byte,
            replacement.iter().copied(),
        );
        let old_tree = self.tree.clone();
        let new_tree = self
            .parser
            .parse(&self.source, Some(&old_tree))
            .context("Tree-sitter incremental parse failed")?;
        let changed = old_tree
            .changed_ranges(&new_tree)
            .map(|range| StructuralRange {
                kind: "changed_range".into(),
                name: None,
                start_byte: range.start_byte,
                end_byte: range.end_byte,
                start_row: range.start_point.row,
                end_row: range.end_point.row,
            })
            .collect();
        self.tree = new_tree;
        self.generation = self.generation.saturating_add(1);
        Ok(changed)
    }

    pub fn generation(&self) -> u64 {
        self.generation
    }
    pub fn grammar(&self) -> Grammar {
        self.grammar
    }
    pub fn source(&self) -> &[u8] {
        &self.source
    }
}

pub fn analyze_postgres_sql(source: &str) -> Result<PostgresAnalysis> {
    let parsed = pg_query::parse(source).context("PostgreSQL parser rejected the statement")?;
    let normalized = pg_query::normalize(source).context("normalizing PostgreSQL SQL")?;
    let fingerprint = pg_query::fingerprint(source)
        .context("fingerprinting PostgreSQL SQL")?
        .hex;
    let mut relations = parsed.tables();
    relations.sort();
    relations.dedup();
    Ok(PostgresAnalysis {
        valid: true,
        normalized,
        fingerprint,
        relations,
        statement_count: parsed.protobuf.stmts.len(),
        engine: "pg_query/libpg_query".into(),
        engine_version: PG_QUERY_ENGINE_VERSION.into(),
    })
}

fn collect_shell_commands(node: Node<'_>, source: &[u8], output: &mut Vec<ShellCommand>) {
    if matches!(
        node.kind(),
        "command" | "redirected_statement" | "pipeline" | "list"
    ) {
        let text = node.utf8_text(source).unwrap_or_default().to_owned();
        if !text.trim().is_empty() {
            output.push(ShellCommand {
                has_redirect: text.contains('>') || text.contains('<'),
                has_substitution: text.contains("$(") || text.contains('`'),
                has_pipeline: text.contains('|'),
                source: text,
                start_byte: node.start_byte(),
                end_byte: node.end_byte(),
                start_row: node.start_position().row,
                end_row: node.end_position().row,
            });
        }
    }
    let mut cursor = node.walk();
    if cursor.goto_first_child() {
        loop {
            collect_shell_commands(cursor.node(), source, output);
            if !cursor.goto_next_sibling() {
                break;
            }
        }
    }
}

pub fn extract_shell_commands(source: &str) -> Result<Vec<ShellCommand>> {
    let mut parser = TreeSitterParser::new();
    parser
        .set_language(&tree_sitter_bash::LANGUAGE.into())
        .context("loading Tree-sitter Bash grammar")?;
    let tree = parser
        .parse(source.as_bytes(), None)
        .context("Tree-sitter Bash returned no tree")?;
    if tree.root_node().has_error() {
        bail!("shell source contains syntax errors");
    }
    let mut output = Vec::new();
    collect_shell_commands(tree.root_node(), source.as_bytes(), &mut output);
    output.sort_by_key(|entry| (entry.start_byte, entry.end_byte));
    output.dedup_by(|left, right| {
        left.start_byte == right.start_byte && left.end_byte == right.end_byte
    });
    Ok(output)
}

pub fn parser_fingerprint(parts: &[&str]) -> String {
    let mut hasher = Hasher::new();
    for part in parts {
        hasher.update(part.as_bytes());
        hasher.update(&[0]);
    }
    hasher.finalize().to_hex().to_string()
}

pub fn point_for_offset(source: &[u8], offset: usize) -> Point {
    let prefix = &source[..offset.min(source.len())];
    let row = prefix.iter().filter(|byte| **byte == b'\n').count();
    let column = prefix
        .iter()
        .rposition(|byte| *byte == b'\n')
        .map_or(prefix.len(), |position| prefix.len() - position - 1);
    Point::new(row, column)
}

fn unix_ms() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis())
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn oxc_extracts_source_ranges_without_claiming_lossless_ast_serialization() {
        let result = parse_oxc(
            Uuid::nil(),
            "src/example.ts",
            "export function greet(name: string) { return `hi ${name}`; }",
            1,
            "default",
        )
        .unwrap();
        assert_eq!(result.engine, "oxc");
        assert!(
            result
                .structural_ranges
                .iter()
                .any(|entry| entry.kind == "function_declaration")
        );
        assert_eq!(
            result
                .program
                .as_ref()
                .and_then(|value| value.get("kind"))
                .and_then(serde_json::Value::as_str),
            Some("Program")
        );
    }

    #[test]
    fn postgres_analysis_uses_native_postgres_parser() {
        let result = analyze_postgres_sql("select * from public.users where id = 42").unwrap();
        assert!(result.valid);
        assert!(result.relations.iter().any(|name| name.contains("users")));
        assert!(!result.fingerprint.is_empty());
    }

    #[test]
    fn bash_parser_extracts_pipeline_and_redirects_without_execution() {
        let result = extract_shell_commands("printf '%s\\n' hello | grep h > out.txt").unwrap();
        assert!(result.iter().any(|entry| entry.has_pipeline));
        assert!(result.iter().any(|entry| entry.has_redirect));
    }
}
