//! Native repository intelligence for Soleaux.
//!
//! The crate intentionally separates fast structural analysis from user-facing
//! source rewrites. Oxc and Tree-sitter produce source ranges and structural
//! facts; safe edits are applied as hash-guarded source patches by the edit
//! service, then formatted and revalidated.

pub mod context;
pub mod context_v2;
mod extraction;
pub mod governance;
pub mod index;
pub mod lsp;
pub mod nextjs;
pub mod nextjs_oxc;
pub mod python_write;
mod query_packs;
pub mod shell_policy;
pub mod turbo_next_matrix;
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

/// Deterministic envelope budgets. Extraction truncates beyond them and
/// reports the truncation as a warning diagnostic.
pub const MAX_STRUCTURAL_RANGES: usize = 16_384;
pub const MAX_REFERENCE_RANGES: usize = 2_048;
pub const MAX_MODULE_EDGES: usize = 4_096;
pub const MAX_SUMMARY_TOP_LEVEL: usize = 64;

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

/// One import edge of the file's module graph, flattened from the parser's
/// ECMAScript module record. Byte offsets follow `range_encoding`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ModuleImport {
    pub module_request: String,
    /// Exported name on the requested module; `None` for default, namespace,
    /// side-effect, and dynamic imports.
    pub imported_name: Option<String>,
    /// Local binding; `None` for side-effect and dynamic imports.
    pub local_name: Option<String>,
    /// One of `named`, `default`, `namespace`, `side_effect`, `dynamic`.
    pub kind: String,
    pub is_type: bool,
    pub start_byte: usize,
    pub end_byte: usize,
}

/// One export edge of the file's module graph.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ModuleExport {
    /// Name visible to importers; `None` for `export *` without an alias.
    pub export_name: Option<String>,
    /// Local binding backing the export; `None` for re-exports.
    pub local_name: Option<String>,
    /// Name imported from `module_request` for re-exports; `*` for star forms.
    pub imported_name: Option<String>,
    /// Requested module for re-exports and star exports.
    pub module_request: Option<String>,
    /// One of `named`, `default`, `re_export`, `star`.
    pub kind: String,
    pub is_type: bool,
    pub start_byte: usize,
    pub end_byte: usize,
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
    #[serde(default)]
    pub imports: Vec<ModuleImport>,
    #[serde(default)]
    pub exports: Vec<ModuleExport>,
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
    pub errors: Vec<String>,
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
                    + (value.imports.len() + value.exports.len()) * 96
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

/// Cache and envelope identity of the Oxc pipeline for a file: the Oxc engine
/// plus the tree-sitter grammar and query pack that back the damaged-source
/// fallback.
pub fn oxc_grammar_hash(grammar: Grammar) -> String {
    parser_fingerprint(&[
        "oxc",
        OXC_ENGINE_VERSION,
        grammar.version(),
        TREE_SITTER_ENGINE_VERSION,
        &query_packs::pack_fingerprint(grammar),
    ])
}

/// Cache and envelope identity of the tree-sitter pipeline for a grammar,
/// including the embedded query pack content.
pub fn tree_sitter_grammar_hash(grammar: Grammar) -> String {
    parser_fingerprint(&[
        grammar.version(),
        TREE_SITTER_ENGINE_VERSION,
        &query_packs::pack_fingerprint(grammar),
    ])
}

fn enforce_range_budget(ranges: &mut Vec<StructuralRange>, diagnostics: &mut Vec<Diagnostic>) {
    if ranges.len() > MAX_STRUCTURAL_RANGES {
        let dropped = ranges.len() - MAX_STRUCTURAL_RANGES;
        ranges.truncate(MAX_STRUCTURAL_RANGES);
        diagnostics.push(Diagnostic {
            message: format!(
                "structural ranges truncated: {dropped} dropped beyond the {MAX_STRUCTURAL_RANGES} budget"
            ),
            severity: "warning".into(),
            start_byte: None,
            end_byte: None,
        });
    }
}

fn enforce_module_edge_budget<T>(
    edges: &mut Vec<T>,
    diagnostics: &mut Vec<Diagnostic>,
    label: &str,
) {
    if edges.len() > MAX_MODULE_EDGES {
        let dropped = edges.len() - MAX_MODULE_EDGES;
        edges.truncate(MAX_MODULE_EDGES);
        diagnostics.push(Diagnostic {
            message: format!(
                "{label} truncated: {dropped} dropped beyond the {MAX_MODULE_EDGES} budget"
            ),
            severity: "warning".into(),
            start_byte: None,
            end_byte: None,
        });
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
        .iter()
        .map(|error| {
            let label = error.labels.first();
            Diagnostic {
                message: error.to_string(),
                severity: "error".into(),
                start_byte: label.map(|label| label.offset() as usize),
                end_byte: label.map(|label| (label.offset() + label.len()) as usize),
            }
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
    let lines = extraction::LineIndex::new(source);
    let mut structural_ranges = if parsed.panicked {
        // Oxc emitted no AST; the tree-sitter query pack keeps structure
        // degrading instead of disappearing.
        let mut tree_parser = TreeSitterParser::new();
        tree_parser
            .set_language(&grammar.language())
            .context("loading TypeScript grammar")?;
        let tree = tree_parser
            .parse(source.as_bytes(), None)
            .context("Tree-sitter returned no tree")?;
        query_packs::collect_structure(grammar, &tree, source.as_bytes(), &mut diagnostics)?
    } else {
        extraction::collect_structure(&parsed.program, &lines)
    };
    let mut imports = extraction::module_imports(&parsed.module_record, source);
    let mut exports = extraction::module_exports(&parsed.module_record);
    enforce_module_edge_budget(&mut imports, &mut diagnostics, "imports");
    enforce_module_edge_budget(&mut exports, &mut diagnostics, "exports");
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
    enforce_range_budget(&mut structural_ranges, &mut diagnostics);
    let language_label = if source_type.is_typescript() {
        "typescript"
    } else {
        "javascript"
    };
    let program = extraction::program_summary(
        language_label,
        parsed.program.body.len(),
        parsed.panicked,
        &structural_ranges,
        &imports,
        &exports,
    );

    let hash = content_hash(source.as_bytes());
    Ok(ParseEnvelope {
        workspace_id,
        relative_path: relative_path.into(),
        language: language_label.into(),
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
        grammar_hash: oxc_grammar_hash(grammar),
        config_fingerprint: config_fingerprint.into(),
        generation,
        parse_duration_us: started.elapsed().as_micros().try_into().unwrap_or(u64::MAX),
        range_encoding: "utf8-bytes-zero-based".into(),
        errors: diagnostics.clone(),
        diagnostics,
        structural_ranges,
        imports,
        exports,
        // The process boundary intentionally carries extracted structure, not a
        // purported lossless serialization of Oxc's arena-backed AST.
        program: Some(program),
        tree: None,
        sql_ast: None,
        bash_ast: None,
        query_capabilities: vec![
            "symbols".into(),
            "imports".into(),
            "exports".into(),
            "jsx_components".into(),
            "react_hooks".into(),
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
    let mut diagnostics = if tree.root_node().has_error() {
        let mut collected = query_packs::error_diagnostics(&tree);
        if collected.is_empty() {
            collected.push(Diagnostic {
                message: "Tree-sitter recovered from one or more syntax errors".into(),
                severity: "error".into(),
                start_byte: None,
                end_byte: None,
            });
        }
        collected
    } else {
        Vec::new()
    };
    let mut structural_ranges =
        query_packs::collect_structure(grammar, &tree, source.as_bytes(), &mut diagnostics)?;
    enforce_range_budget(&mut structural_ranges, &mut diagnostics);
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
        grammar_hash: tree_sitter_grammar_hash(grammar),
        config_fingerprint: config_fingerprint.into(),
        generation,
        parse_duration_us: started.elapsed().as_micros().try_into().unwrap_or(u64::MAX),
        range_encoding: "utf8-bytes-zero-based".into(),
        diagnostics: diagnostics.clone(),
        errors: diagnostics,
        structural_ranges,
        imports: Vec::new(),
        exports: Vec::new(),
        program: None,
        tree: Some(
            serde_json::json!({"rootKind": tree.root_node().kind(), "hasError": tree.root_node().has_error()}),
        ),
        sql_ast: None,
        bash_ast: None,
        query_capabilities: query_packs::capabilities(grammar),
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
    let parsed = match pg_query::parse(source) {
        Ok(parsed) => parsed,
        Err(error) => {
            return Ok(PostgresAnalysis {
                valid: false,
                normalized: String::new(),
                fingerprint: String::new(),
                relations: Vec::new(),
                statement_count: 0,
                errors: vec![error.to_string()],
                engine: "pg_query/libpg_query".into(),
                engine_version: PG_QUERY_ENGINE_VERSION.into(),
            });
        }
    };
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
        errors: Vec::new(),
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
        let source = "export function greet(name: string) { return `hi ${name}`; }";
        let result = parse_oxc(Uuid::nil(), "src/example.ts", source, 1, "default").unwrap();
        assert_eq!(result.engine, "oxc");
        let function = result
            .structural_ranges
            .iter()
            .find(|entry| entry.kind == "function_declaration")
            .expect("function range");
        assert_eq!(function.name.as_deref(), Some("greet"));
        assert_eq!(function.start_byte, source.find("function").unwrap());
        assert_eq!(function.end_byte, source.len());
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
    fn oxc_extracts_symbols_imports_exports_from_the_real_ast() {
        let source = concat!(
            "import React from \"react\";\n",
            "import { useMemo, type Config } from \"./config\";\n",
            "import * as fs from \"node:fs\";\n",
            "import \"./side-effect\";\n",
            "const lazy = import(\"./lazy\");\n",
            "export const answer = 42;\n",
            "export default class Engine {\n",
            "  start(): void {}\n",
            "}\n",
            "export { useMemo as reexported } from \"./config\";\n",
            "export * from \"./star\";\n",
            "export interface Shape { sides: number }\n",
            "export type Alias = Shape;\n",
            "export enum Mode { On, Off }\n",
            "namespace Internal { export const flag = true; }\n",
        );
        let result = parse_oxc(Uuid::nil(), "src/module.ts", source, 1, "default").unwrap();
        assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);

        let kind_names = |kind: &str| {
            result
                .structural_ranges
                .iter()
                .filter(|entry| entry.kind == kind)
                .filter_map(|entry| entry.name.clone())
                .collect::<Vec<_>>()
        };
        assert_eq!(kind_names("class_declaration"), vec!["Engine"]);
        assert_eq!(kind_names("method_definition"), vec!["start"]);
        assert_eq!(kind_names("interface_declaration"), vec!["Shape"]);
        assert_eq!(kind_names("type_alias_declaration"), vec!["Alias"]);
        assert_eq!(kind_names("enum_declaration"), vec!["Mode"]);
        assert_eq!(kind_names("internal_module"), vec!["Internal"]);
        assert!(kind_names("variable_declarator").contains(&"answer".to_owned()));
        for range in &result.structural_ranges {
            assert!(range.end_byte <= source.len());
            assert!(range.start_byte <= range.end_byte);
        }

        let import_kinds = result
            .imports
            .iter()
            .map(|import| (import.kind.as_str(), import.module_request.as_str()))
            .collect::<Vec<_>>();
        assert!(import_kinds.contains(&("default", "react")));
        assert!(import_kinds.contains(&("named", "./config")));
        assert!(import_kinds.contains(&("namespace", "node:fs")));
        assert!(import_kinds.contains(&("side_effect", "./side-effect")));
        assert!(import_kinds.contains(&("dynamic", "./lazy")));
        let type_import = result
            .imports
            .iter()
            .find(|import| import.imported_name.as_deref() == Some("Config"))
            .expect("type import");
        assert!(type_import.is_type);

        let export_kinds = result
            .exports
            .iter()
            .map(|export| export.kind.as_str())
            .collect::<Vec<_>>();
        assert!(export_kinds.contains(&"named"));
        assert!(export_kinds.contains(&"default"));
        assert!(export_kinds.contains(&"re_export"));
        assert!(export_kinds.contains(&"star"));
        let reexport = result
            .exports
            .iter()
            .find(|export| export.kind == "re_export")
            .expect("re-export");
        assert_eq!(reexport.export_name.as_deref(), Some("reexported"));
        assert_eq!(reexport.imported_name.as_deref(), Some("useMemo"));
        assert_eq!(reexport.module_request.as_deref(), Some("./config"));

        let counts = result
            .program
            .as_ref()
            .and_then(|value| value.get("counts"))
            .cloned()
            .expect("program counts");
        assert_eq!(
            counts.get("imports").and_then(serde_json::Value::as_u64),
            Some(result.imports.len() as u64)
        );
        assert_eq!(
            result.query_capabilities,
            vec![
                "symbols",
                "imports",
                "exports",
                "jsx_components",
                "react_hooks",
                "source_ranges"
            ]
        );
    }

    #[test]
    fn oxc_extracts_jsx_components_and_react_hooks() {
        let source = concat!(
            "import React, { useState } from \"react\";\n",
            "export function App() {\n",
            "  const [count, setCount] = useState(0);\n",
            "  React.useEffect(() => {}, []);\n",
            "  return <main><Widget.Panel value={count} /><div onClick={() => setCount(1)} /></main>;\n",
            "}\n",
        );
        let result = parse_oxc(Uuid::nil(), "src/app.tsx", source, 1, "default").unwrap();
        assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
        let components = result
            .structural_ranges
            .iter()
            .filter(|entry| entry.kind == "jsx_component")
            .filter_map(|entry| entry.name.clone())
            .collect::<Vec<_>>();
        assert_eq!(components, vec!["Widget.Panel"]);
        let hooks = result
            .structural_ranges
            .iter()
            .filter(|entry| entry.kind == "react_hook")
            .filter_map(|entry| entry.name.clone())
            .collect::<Vec<_>>();
        assert_eq!(hooks, vec!["useState", "useEffect"]);
        let use_state = result
            .structural_ranges
            .iter()
            .find(|entry| entry.name.as_deref() == Some("useState") && entry.kind == "react_hook")
            .expect("useState range");
        assert_eq!(use_state.start_byte, source.find("useState(0)").unwrap());
    }

    #[test]
    fn tree_sitter_query_pack_extracts_python_structure() {
        let source = concat!(
            "import os.path\n",
            "from typing import Any\n",
            "\n",
            "@decorated\n",
            "def handler(payload):\n",
            "    return os.path.join(payload)\n",
            "\n",
            "class Service:\n",
            "    def run(self):\n",
            "        return handler({})\n",
        );
        let result = parse_tree_sitter(
            Uuid::nil(),
            "src/service.py",
            source,
            Grammar::Python,
            1,
            "default",
        )
        .unwrap();
        assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
        let kinds = result
            .structural_ranges
            .iter()
            .map(|entry| entry.kind.as_str())
            .collect::<Vec<_>>();
        assert!(kinds.contains(&"function_definition"));
        assert!(kinds.contains(&"class_definition"));
        assert!(kinds.contains(&"decorated_definition"));
        assert!(kinds.contains(&"import_statement"));
        assert!(kinds.contains(&"import_from_statement"));
        assert!(kinds.contains(&"definition.function"));
        assert!(kinds.contains(&"definition.class"));
        assert!(kinds.contains(&"reference.call"));
        let import = result
            .structural_ranges
            .iter()
            .find(|entry| entry.kind == "import_statement")
            .expect("import range");
        assert_eq!(import.name.as_deref(), Some("os.path"));
        assert_eq!(
            result.query_capabilities,
            vec![
                "syntax_query",
                "incremental_reparse",
                "source_ranges",
                "definitions",
                "references"
            ]
        );
    }

    #[test]
    fn tree_sitter_query_pack_extracts_bash_structure() {
        let source = concat!(
            "TARGET=dist\n",
            "build() {\n",
            "  cargo build --release\n",
            "}\n",
            "build\n",
        );
        let result = parse_tree_sitter(
            Uuid::nil(),
            "scripts/build.sh",
            source,
            Grammar::Bash,
            1,
            "default",
        )
        .unwrap();
        assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
        let named = |kind: &str| {
            result
                .structural_ranges
                .iter()
                .filter(|entry| entry.kind == kind)
                .filter_map(|entry| entry.name.clone())
                .collect::<Vec<_>>()
        };
        assert_eq!(named("function_definition"), vec!["build"]);
        assert_eq!(named("variable_assignment"), vec!["TARGET"]);
        assert_eq!(named("definition.variable"), vec!["TARGET"]);
        assert!(named("reference.call").contains(&"cargo".to_owned()));
        assert!(named("reference.call").contains(&"build".to_owned()));
    }

    #[test]
    fn tree_sitter_injection_query_marks_tagged_template_content() {
        let source = "const q = sql`select * from users where id = ${id}`;\n";
        let result = parse_tree_sitter(
            Uuid::nil(),
            "src/query.ts",
            source,
            Grammar::TypeScript,
            1,
            "default",
        )
        .unwrap();
        let injection = result
            .structural_ranges
            .iter()
            .find(|entry| entry.kind == "injection")
            .expect("injection range");
        assert_eq!(injection.name.as_deref(), Some("sql"));
        assert_eq!(injection.start_byte, source.find('`').unwrap());
        assert_eq!(injection.end_byte, source.rfind('`').unwrap() + 1);
        assert!(result.query_capabilities.contains(&"injections".to_owned()));
        let python = parse_tree_sitter(
            Uuid::nil(),
            "src/empty.py",
            "x = 1\n",
            Grammar::Python,
            1,
            "default",
        )
        .unwrap();
        assert!(!python.query_capabilities.contains(&"injections".to_owned()));
    }

    #[test]
    fn postgres_analysis_uses_native_postgres_parser() {
        let result = analyze_postgres_sql("select * from public.users where id = 42").unwrap();
        assert!(result.valid);
        assert!(result.relations.iter().any(|name| name.contains("users")));
        assert!(!result.fingerprint.is_empty());
        assert!(result.errors.is_empty());
    }

    #[test]
    fn postgres_analysis_returns_invalid_sql_as_typed_data() {
        let result = analyze_postgres_sql("select from where").unwrap();
        assert!(!result.valid);
        assert!(result.normalized.is_empty());
        assert!(result.fingerprint.is_empty());
        assert!(result.relations.is_empty());
        assert_eq!(result.statement_count, 0);
        assert!(!result.errors.is_empty());
    }

    #[test]
    fn bash_parser_extracts_pipeline_and_redirects_without_execution() {
        let result = extract_shell_commands("printf '%s\\n' hello | grep h > out.txt").unwrap();
        assert!(result.iter().any(|entry| entry.has_pipeline));
        assert!(result.iter().any(|entry| entry.has_redirect));
    }
}
