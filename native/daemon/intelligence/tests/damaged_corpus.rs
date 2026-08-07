//! Damaged-file corpus: parse envelopes stay bounded, diagnostics are
//! reported, structure degrades without panicking, and incremental reparse
//! still yields changed ranges.

use soleaux_intelligence::{
    Grammar, IncrementalTree, MAX_REFERENCE_RANGES, MAX_STRUCTURAL_RANGES, ParseEnvelope,
    parse_oxc, parse_tree_sitter, point_for_offset,
};
use tree_sitter::InputEdit;
use uuid::Uuid;

const TRUNCATED_TS: &str = include_str!("fixtures/damaged/truncated.ts");
const GARBLED_TSX: &str = include_str!("fixtures/damaged/garbled.tsx");
const TRUNCATED_PY: &str = include_str!("fixtures/damaged/truncated.py");
const GARBLED_SH: &str = include_str!("fixtures/damaged/garbled.sh");

fn assert_bounded(envelope: &ParseEnvelope, source_len: usize) {
    assert!(envelope.structural_ranges.len() <= MAX_STRUCTURAL_RANGES);
    for range in &envelope.structural_ranges {
        assert!(range.start_byte <= range.end_byte);
        assert!(range.end_byte <= source_len);
    }
    for diagnostic in &envelope.diagnostics {
        if let (Some(start), Some(end)) = (diagnostic.start_byte, diagnostic.end_byte) {
            assert!(start <= end);
            assert!(end <= source_len);
        }
    }
    assert_eq!(envelope.byte_length, source_len);
}

#[test]
fn truncated_typescript_reports_diagnostics_and_keeps_structure() {
    let result = parse_oxc(
        Uuid::nil(),
        "damaged/truncated.ts",
        TRUNCATED_TS,
        1,
        "default",
    )
    .expect("damaged source must still produce an envelope");
    assert!(!result.diagnostics.is_empty());
    assert_bounded(&result, TRUNCATED_TS.len());
    assert_eq!(result.structural_ranges[0].kind, "program");
    assert!(
        result.structural_ranges.len() > 1,
        "structure should degrade, not disappear: {:?}",
        result.structural_ranges
    );
    let program = result.program.expect("program summary");
    assert_eq!(
        program.get("panicked").and_then(serde_json::Value::as_bool),
        Some(true),
        "oxc 0.142 panics on this truncation; the summary must say so"
    );
    assert!(program.get("counts").is_some());
    // The unterminated call swallows the enclosing function into an ERROR
    // region; the import and the intact declarator are what tree-sitter
    // recovers.
    let kinds = result
        .structural_ranges
        .iter()
        .map(|range| range.kind.as_str())
        .collect::<Vec<_>>();
    assert!(
        kinds.contains(&"import_statement"),
        "tree-sitter fallback structure must survive the oxc panic: {:?}",
        result.structural_ranges
    );
    assert!(
        result
            .structural_ranges
            .iter()
            .any(|range| range.kind == "variable_declarator"
                && range.name.as_deref() == Some("handle")),
        "intact declarator should survive: {:?}",
        result.structural_ranges
    );
    assert!(
        result
            .diagnostics
            .iter()
            .any(|diagnostic| diagnostic.start_byte.is_some()),
        "expected at least one spanned diagnostic: {:?}",
        result.diagnostics
    );
}

#[test]
fn garbled_tsx_reports_diagnostics_and_keeps_structure() {
    let result = parse_oxc(
        Uuid::nil(),
        "damaged/garbled.tsx",
        GARBLED_TSX,
        1,
        "default",
    )
    .expect("garbled source must still produce an envelope");
    assert!(!result.diagnostics.is_empty());
    assert_bounded(&result, GARBLED_TSX.len());
    assert_eq!(result.structural_ranges[0].kind, "program");
}

#[test]
fn truncated_python_reports_spanned_diagnostics_without_panicking() {
    let result = parse_tree_sitter(
        Uuid::nil(),
        "damaged/truncated.py",
        TRUNCATED_PY,
        Grammar::Python,
        1,
        "default",
    )
    .expect("damaged source must still produce an envelope");
    assert!(!result.diagnostics.is_empty());
    assert!(
        result
            .diagnostics
            .iter()
            .any(|diagnostic| diagnostic.start_byte.is_some()),
        "expected at least one spanned diagnostic: {:?}",
        result.diagnostics
    );
    assert_bounded(&result, TRUNCATED_PY.len());
    assert!(
        result
            .structural_ranges
            .iter()
            .any(|range| range.kind == "function_definition"),
        "intact prefix structure should survive: {:?}",
        result.structural_ranges
    );
}

#[test]
fn garbled_bash_reports_diagnostics_without_panicking() {
    let result = parse_tree_sitter(
        Uuid::nil(),
        "damaged/garbled.sh",
        GARBLED_SH,
        Grammar::Bash,
        1,
        "default",
    )
    .expect("damaged source must still produce an envelope");
    assert!(!result.diagnostics.is_empty());
    assert_bounded(&result, GARBLED_SH.len());
}

#[test]
fn incremental_tree_still_yields_changed_ranges_on_damaged_source() {
    let mut tree =
        IncrementalTree::parse(Grammar::TypeScript, TRUNCATED_TS.as_bytes()).expect("parse");
    let replacement = b"const repaired = 1;\n";
    let edit_start = 0usize;
    let edit_old_end = TRUNCATED_TS.find('\n').expect("newline") + 1;
    let edit = InputEdit {
        start_byte: edit_start,
        old_end_byte: edit_old_end,
        new_end_byte: replacement.len(),
        start_position: point_for_offset(TRUNCATED_TS.as_bytes(), edit_start),
        old_end_position: point_for_offset(TRUNCATED_TS.as_bytes(), edit_old_end),
        new_end_position: point_for_offset(replacement, replacement.len()),
    };
    let changed = tree.apply_edit(edit, replacement).expect("apply edit");
    assert!(
        !changed.is_empty(),
        "incremental reparse of damaged source must report changed ranges"
    );
    for range in &changed {
        assert_eq!(range.kind, "changed_range");
        assert!(range.start_byte <= range.end_byte);
    }
    assert_eq!(tree.generation(), 2);
}

#[test]
fn reference_budget_bounds_adversarial_envelopes() {
    let source = "f();\n".repeat(MAX_REFERENCE_RANGES + 500);
    let result = parse_tree_sitter(
        Uuid::nil(),
        "adversarial/calls.ts",
        &source,
        Grammar::TypeScript,
        1,
        "default",
    )
    .expect("parse");
    let references = result
        .structural_ranges
        .iter()
        .filter(|range| range.kind == "reference.call")
        .count();
    assert_eq!(references, MAX_REFERENCE_RANGES);
    assert!(
        result
            .diagnostics
            .iter()
            .any(|diagnostic| diagnostic.severity == "warning"
                && diagnostic.message.contains("reference ranges truncated")),
        "truncation must be reported: {:?}",
        result.diagnostics
    );
    assert_bounded(&result, source.len());
}

#[test]
fn oversized_source_never_inlines_utf8() {
    let source = "const value = 1;\n".repeat(8_000);
    assert!(source.len() > 64 * 1024);
    let result = parse_oxc(Uuid::nil(), "large/flat.ts", &source, 1, "default").expect("parse");
    assert!(result.source.inline_utf8.is_none());
    assert_bounded(&result, source.len());
}
