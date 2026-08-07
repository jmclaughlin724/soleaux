//! Embedded tree-sitter query packs: structural kinds, definitions,
//! references, and language injections, compiled once per grammar against the
//! pinned dialects.

use anyhow::{Result, anyhow};
use std::sync::OnceLock;
use tree_sitter::{Query, QueryCursor, StreamingIterator, Tree};

use crate::{Diagnostic, Grammar, MAX_REFERENCE_RANGES, StructuralRange};

const TYPESCRIPT_STRUCTURE: &str = include_str!("../queries/typescript.scm");
const TSX_STRUCTURE_EXTENSION: &str = include_str!("../queries/tsx.scm");
const PYTHON_STRUCTURE: &str = include_str!("../queries/python.scm");
const BASH_STRUCTURE: &str = include_str!("../queries/bash.scm");
const TYPESCRIPT_INJECTIONS: &str = include_str!("../queries/typescript-injections.scm");

const MAX_ERROR_DIAGNOSTICS: usize = 64;

/// Node kinds whose text is a usable symbol name; pattern nodes (destructuring
/// targets, computed keys) match `(_)` name captures but carry no single name.
const NAME_NODE_KINDS: &[&str] = &[
    "identifier",
    "type_identifier",
    "property_identifier",
    "private_property_identifier",
    "nested_identifier",
    "dotted_name",
    "word",
    "variable_name",
    "command_name",
];

/// Tag allowlist for template-literal injections, mapped to a canonical
/// language name.
const INJECTION_LANGUAGES: &[(&str, &str)] = &[
    ("css", "css"),
    ("gql", "graphql"),
    ("graphql", "graphql"),
    ("html", "html"),
    ("json", "json"),
    ("md", "markdown"),
    ("markdown", "markdown"),
    ("sql", "sql"),
];

fn structure_source(grammar: Grammar) -> String {
    match grammar {
        Grammar::TypeScript => TYPESCRIPT_STRUCTURE.to_owned(),
        Grammar::Tsx => format!("{TYPESCRIPT_STRUCTURE}\n{TSX_STRUCTURE_EXTENSION}"),
        Grammar::Python => PYTHON_STRUCTURE.to_owned(),
        Grammar::Bash => BASH_STRUCTURE.to_owned(),
    }
}

fn injection_source(grammar: Grammar) -> Option<&'static str> {
    match grammar {
        Grammar::TypeScript | Grammar::Tsx => Some(TYPESCRIPT_INJECTIONS),
        Grammar::Python | Grammar::Bash => None,
    }
}

/// Content fingerprint of the embedded pack for a grammar; part of the cache
/// identity so editing a pack invalidates stale envelopes.
pub fn pack_fingerprint(grammar: Grammar) -> String {
    crate::parser_fingerprint(&[
        &structure_source(grammar),
        injection_source(grammar).unwrap_or(""),
    ])
}

/// Query capabilities genuinely backed by the pack for this grammar.
pub fn capabilities(grammar: Grammar) -> Vec<String> {
    let mut capabilities = vec![
        "syntax_query".to_owned(),
        "incremental_reparse".to_owned(),
        "source_ranges".to_owned(),
        "definitions".to_owned(),
        "references".to_owned(),
    ];
    if injection_source(grammar).is_some() {
        capabilities.push("injections".to_owned());
    }
    capabilities
}

type CompiledQuery = std::result::Result<Query, String>;

fn compiled(
    cell: &'static OnceLock<CompiledQuery>,
    grammar: Grammar,
    source: &str,
) -> Result<&'static Query> {
    cell.get_or_init(|| Query::new(&grammar.language(), source).map_err(|error| error.to_string()))
        .as_ref()
        .map_err(|error| anyhow!("compiling embedded {} query pack: {error}", grammar.name()))
}

fn structure_query(grammar: Grammar) -> Result<&'static Query> {
    static TYPESCRIPT: OnceLock<CompiledQuery> = OnceLock::new();
    static TSX: OnceLock<CompiledQuery> = OnceLock::new();
    static PYTHON: OnceLock<CompiledQuery> = OnceLock::new();
    static BASH: OnceLock<CompiledQuery> = OnceLock::new();
    let cell = match grammar {
        Grammar::TypeScript => &TYPESCRIPT,
        Grammar::Tsx => &TSX,
        Grammar::Python => &PYTHON,
        Grammar::Bash => &BASH,
    };
    compiled(cell, grammar, &structure_source(grammar))
}

fn injection_query(grammar: Grammar) -> Result<Option<&'static Query>> {
    static TYPESCRIPT: OnceLock<CompiledQuery> = OnceLock::new();
    static TSX: OnceLock<CompiledQuery> = OnceLock::new();
    let Some(source) = injection_source(grammar) else {
        return Ok(None);
    };
    let cell = match grammar {
        Grammar::TypeScript => &TYPESCRIPT,
        Grammar::Tsx => &TSX,
        Grammar::Python | Grammar::Bash => unreachable!("no injection pack"),
    };
    compiled(cell, grammar, source).map(Some)
}

fn named_text(node: tree_sitter::Node<'_>, source: &[u8]) -> Option<String> {
    if !NAME_NODE_KINDS.contains(&node.kind()) {
        return None;
    }
    node.utf8_text(source).ok().map(str::to_owned)
}

fn jsx_name_text(node: tree_sitter::Node<'_>, source: &[u8]) -> Option<String> {
    if !matches!(node.kind(), "identifier" | "member_expression") {
        return None;
    }
    let text = node.utf8_text(source).ok()?;
    let component = text.contains('.')
        || text
            .chars()
            .next()
            .is_some_and(|first| first.is_ascii_uppercase());
    component.then(|| text.to_owned())
}

/// Run the grammar's structure pack (and injection pack where one exists) over
/// a parsed tree. Reference captures are capped at [`MAX_REFERENCE_RANGES`];
/// a warning diagnostic reports how many were dropped.
pub fn collect_structure(
    grammar: Grammar,
    tree: &Tree,
    source: &[u8],
    diagnostics: &mut Vec<Diagnostic>,
) -> Result<Vec<StructuralRange>> {
    let query = structure_query(grammar)?;
    let capture_names = query.capture_names();
    let mut ranges: Vec<StructuralRange> = Vec::new();
    let mut reference_count = 0usize;
    let mut dropped_references = 0usize;
    let mut cursor = QueryCursor::new();
    let mut matches = cursor.matches(query, tree.root_node(), source);
    while let Some(matched) = matches.next() {
        let name_node = matched
            .captures
            .iter()
            .find(|capture| capture_names[capture.index as usize] == "name")
            .map(|capture| capture.node);
        for capture in matched.captures {
            let capture_name = capture_names[capture.index as usize];
            if capture_name == "name" {
                continue;
            }
            if let Some(reference_name) = capture_name.strip_prefix("reference.") {
                debug_assert!(!reference_name.is_empty());
                if reference_count >= MAX_REFERENCE_RANGES {
                    dropped_references += 1;
                    continue;
                }
                reference_count += 1;
                push_range(
                    &mut ranges,
                    capture_name,
                    named_text(capture.node, source),
                    capture.node,
                );
            } else if capture_name.starts_with("definition.") {
                if let Some(name) = named_text(capture.node, source) {
                    push_range(&mut ranges, capture_name, Some(name), capture.node);
                }
            } else if capture_name == "jsx_component" {
                if let Some(name) = name_node.and_then(|node| jsx_name_text(node, source)) {
                    push_range(&mut ranges, capture_name, Some(name), capture.node);
                }
            } else {
                let name = name_node.and_then(|node| named_text(node, source));
                push_range(&mut ranges, capture_name, name, capture.node);
            }
        }
    }
    if let Some(query) = injection_query(grammar)? {
        let capture_names = query.capture_names();
        let mut cursor = QueryCursor::new();
        let mut matches = cursor.matches(query, tree.root_node(), source);
        while let Some(matched) = matches.next() {
            let language = matched
                .captures
                .iter()
                .find(|capture| capture_names[capture.index as usize] == "injection.language")
                .and_then(|capture| capture.node.utf8_text(source).ok());
            let content = matched
                .captures
                .iter()
                .find(|capture| capture_names[capture.index as usize] == "injection.content")
                .map(|capture| capture.node);
            let canonical = language.and_then(|tag| {
                INJECTION_LANGUAGES
                    .iter()
                    .find(|(name, _)| *name == tag)
                    .map(|(_, canonical)| *canonical)
            });
            if let (Some(language), Some(node)) = (canonical, content) {
                push_range(&mut ranges, "injection", Some(language.to_owned()), node);
            }
        }
    }
    if dropped_references > 0 {
        diagnostics.push(Diagnostic {
            message: format!(
                "reference ranges truncated: {dropped_references} dropped beyond the {MAX_REFERENCE_RANGES} budget"
            ),
            severity: "warning".into(),
            start_byte: None,
            end_byte: None,
        });
    }
    ranges.sort_by(|left, right| {
        left.start_byte
            .cmp(&right.start_byte)
            .then(right.end_byte.cmp(&left.end_byte))
            .then(left.kind.cmp(&right.kind))
    });
    ranges.dedup_by(|left, right| {
        left.kind == right.kind
            && left.start_byte == right.start_byte
            && left.end_byte == right.end_byte
    });
    Ok(ranges)
}

fn push_range(
    ranges: &mut Vec<StructuralRange>,
    kind: &str,
    name: Option<String>,
    node: tree_sitter::Node<'_>,
) {
    ranges.push(StructuralRange {
        kind: kind.to_owned(),
        name,
        start_byte: node.start_byte(),
        end_byte: node.end_byte(),
        start_row: node.start_position().row,
        end_row: node.end_position().row,
    });
}

/// Collect ERROR and missing nodes as spanned diagnostics, capped at
/// [`MAX_ERROR_DIAGNOSTICS`].
pub fn error_diagnostics(tree: &Tree) -> Vec<Diagnostic> {
    let mut diagnostics = Vec::new();
    let mut stack = vec![tree.root_node()];
    while let Some(node) = stack.pop() {
        if diagnostics.len() >= MAX_ERROR_DIAGNOSTICS {
            break;
        }
        if node.is_error() {
            diagnostics.push(Diagnostic {
                message: "Tree-sitter syntax error".into(),
                severity: "error".into(),
                start_byte: Some(node.start_byte()),
                end_byte: Some(node.end_byte()),
            });
            continue;
        }
        if node.is_missing() {
            diagnostics.push(Diagnostic {
                message: format!("Tree-sitter missing {}", node.kind()),
                severity: "error".into(),
                start_byte: Some(node.start_byte()),
                end_byte: Some(node.end_byte()),
            });
            continue;
        }
        if node.has_error() {
            let mut cursor = node.walk();
            for child in node.children(&mut cursor) {
                stack.push(child);
            }
        }
    }
    diagnostics.sort_by_key(|diagnostic| (diagnostic.start_byte, diagnostic.end_byte));
    diagnostics
}
