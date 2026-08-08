//! Formatting-preserving Python writes over the native LibCST parser.
//!
//! Reads keep their existing owners (tree-sitter for structure, BasedPyright
//! for semantics). This module owns the write side: every Python write remains
//! a hash-guarded source-range patch — never whole-file CST regeneration — and
//! LibCST certifies it by round-tripping both the preimage and the postimage
//! through the lossless CST and by proving byte fidelity of every unmodified
//! region. Certification fails closed: a file or patch the CST cannot fully
//! explain is reported as uncertified, with the reason, instead of being
//! silently accepted.

use anyhow::{Context, Result, bail};
use libcst_native::{Codegen, CodegenState, Module, parse_module, parse_statement};
use serde::{Deserialize, Serialize};
use tree_sitter::{Node, Parser as TreeSitterParser};

pub const PYTHON_WRITE_ENGINE: &str = "libcst";
pub const PYTHON_WRITE_ENGINE_VERSION: &str = "1.8.6";

/// One source-range replacement, byte-addressed against the preimage
/// (`utf8-bytes-zero-based`, matching every other Soleaux range).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct SourcePatch {
    pub start_byte: usize,
    pub end_byte: usize,
    pub replacement: String,
}

/// Outcome of one lossless-CST round trip.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct PythonRoundTrip {
    pub parsed: bool,
    /// `parse(source)` regenerated to the exact input bytes.
    pub lossless: bool,
    pub byte_length: usize,
    /// A leading UTF-8 BOM sits outside the CST; it is stripped before the
    /// round trip and reported here.
    pub had_utf8_bom: bool,
    pub error: Option<String>,
}

/// Verification of a set of patches against one Python preimage.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct PythonWriteVerification {
    pub engine: String,
    pub engine_version: String,
    /// True only when the preimage round-trips, the postimage round-trips,
    /// and every byte outside the patched ranges is unchanged.
    pub certified: bool,
    pub preimage: PythonRoundTrip,
    pub postimage: PythonRoundTrip,
    pub unmodified_regions_intact: bool,
    pub postimage_source: String,
    pub diagnostics: Vec<String>,
}

fn generate(module: &Module<'_>) -> String {
    let mut state = CodegenState {
        default_newline: module.default_newline,
        default_indent: module.default_indent,
        ..CodegenState::default()
    };
    module.codegen(&mut state);
    state.to_string()
}

/// Round-trip one Python source through the lossless CST. Invalid Python is
/// typed data, not an error: `parsed` is false and `error` carries the reason.
pub fn verify_roundtrip(source: &str) -> PythonRoundTrip {
    let had_utf8_bom = source.starts_with('\u{feff}');
    let body = source.strip_prefix('\u{feff}').unwrap_or(source);
    match parse_module(body, None) {
        Ok(module) => {
            let regenerated = generate(&module);
            PythonRoundTrip {
                parsed: true,
                lossless: regenerated == body,
                byte_length: source.len(),
                had_utf8_bom,
                error: None,
            }
        }
        Err(error) => PythonRoundTrip {
            parsed: false,
            lossless: false,
            byte_length: source.len(),
            had_utf8_bom,
            error: Some(error.to_string()),
        },
    }
}

/// Validate that a replacement snippet parses as one Python statement line
/// (including its trailing newline). Used to gate structural-rewrite
/// replacements before a preview is created.
pub fn validate_statement_snippet(snippet: &str) -> Result<()> {
    parse_statement(snippet)
        .map(|_| ())
        .map_err(|error| anyhow::anyhow!("replacement is not a valid Python statement: {error}"))
}

fn ordered_patches(source: &str, patches: &[SourcePatch]) -> Result<Vec<SourcePatch>> {
    if patches.is_empty() {
        bail!("verification requires at least one patch");
    }
    let mut ordered = patches.to_vec();
    ordered.sort_by_key(|patch| (patch.start_byte, patch.end_byte));
    for patch in &ordered {
        if patch.end_byte < patch.start_byte || patch.end_byte > source.len() {
            bail!("patch range is outside the source preimage");
        }
        if !source.is_char_boundary(patch.start_byte) || !source.is_char_boundary(patch.end_byte) {
            bail!("patch range splits a UTF-8 character");
        }
    }
    for pair in ordered.windows(2) {
        if pair[1].start_byte < pair[0].end_byte {
            bail!("patches overlap");
        }
    }
    Ok(ordered)
}

fn apply(source: &str, ordered: &[SourcePatch]) -> String {
    let mut output = String::with_capacity(source.len());
    let mut cursor = 0usize;
    for patch in ordered {
        output.push_str(&source[cursor..patch.start_byte]);
        output.push_str(&patch.replacement);
        cursor = patch.end_byte;
    }
    output.push_str(&source[cursor..]);
    output
}

/// Independently re-derive that every byte outside the patched ranges is
/// unchanged between preimage and postimage.
fn unmodified_regions_intact(source: &str, postimage: &str, ordered: &[SourcePatch]) -> bool {
    let mut pre_cursor = 0usize;
    let mut post_cursor = 0usize;
    for patch in ordered {
        let gap = &source.as_bytes()[pre_cursor..patch.start_byte];
        let Some(post_gap) = postimage
            .as_bytes()
            .get(post_cursor..post_cursor + gap.len())
        else {
            return false;
        };
        if gap != post_gap {
            return false;
        }
        pre_cursor = patch.end_byte;
        post_cursor = post_cursor + gap.len() + patch.replacement.len();
    }
    source.as_bytes().get(pre_cursor..) == postimage.as_bytes().get(post_cursor..)
}

/// Verify a set of source-range patches against a Python preimage.
///
/// Range errors (out of bounds, overlapping, splitting a UTF-8 character) are
/// hard errors because no postimage exists to report on. Everything else is
/// typed data: an unparseable preimage or postimage yields
/// `certified == false` with the failure named in `diagnostics`.
pub fn verify_patches(source: &str, patches: &[SourcePatch]) -> Result<PythonWriteVerification> {
    let ordered = ordered_patches(source, patches)?;
    let preimage = verify_roundtrip(source);
    let postimage_source = apply(source, &ordered);
    let postimage = verify_roundtrip(&postimage_source);
    let regions_intact = unmodified_regions_intact(source, &postimage_source, &ordered);

    let mut diagnostics = Vec::new();
    if !preimage.parsed {
        diagnostics.push(format!(
            "preimage is not parseable Python: {}",
            preimage.error.as_deref().unwrap_or("unknown parse failure")
        ));
    } else if !preimage.lossless {
        diagnostics.push("preimage does not round-trip losslessly through the CST".to_owned());
    }
    if !postimage.parsed {
        diagnostics.push(format!(
            "postimage is not parseable Python: {}",
            postimage
                .error
                .as_deref()
                .unwrap_or("unknown parse failure")
        ));
    } else if !postimage.lossless {
        diagnostics.push("postimage does not round-trip losslessly through the CST".to_owned());
    }
    if !regions_intact {
        diagnostics.push("bytes outside the patched ranges changed".to_owned());
    }

    Ok(PythonWriteVerification {
        engine: PYTHON_WRITE_ENGINE.to_owned(),
        engine_version: PYTHON_WRITE_ENGINE_VERSION.to_owned(),
        certified: preimage.lossless && postimage.lossless && regions_intact,
        preimage,
        postimage,
        unmodified_regions_intact: regions_intact,
        postimage_source,
        diagnostics,
    })
}

fn is_python_identifier(text: &str) -> bool {
    let mut chars = text.chars();
    let Some(first) = chars.next() else {
        return false;
    };
    (first.is_alphabetic() || first == '_')
        && chars.all(|value| value.is_alphanumeric() || value == '_')
}

fn is_module_path(text: &str) -> bool {
    !text.is_empty() && text.split('.').all(is_python_identifier)
}

fn parse_python_tree(source: &str) -> Result<tree_sitter::Tree> {
    let mut parser = TreeSitterParser::new();
    parser
        .set_language(&tree_sitter_python::LANGUAGE.into())
        .context("loading Tree-sitter Python grammar")?;
    parser
        .parse(source.as_bytes(), None)
        .context("Tree-sitter Python returned no tree")
}

fn import_covers(node: Node<'_>, source: &[u8], module: &str, name: Option<&str>) -> bool {
    let text = |node: Node<'_>| node.utf8_text(source).unwrap_or_default();
    match node.kind() {
        // `import a.b` and `import a.b as c` bind the requested module; a
        // plain-module request is satisfied, a `from`-style request is not.
        "import_statement" => {
            name.is_none()
                && named_children(node).any(|child| match child.kind() {
                    "dotted_name" => text(child) == module,
                    "aliased_import" => child
                        .child_by_field_name("name")
                        .is_some_and(|inner| text(inner) == module),
                    _ => false,
                })
        }
        "import_from_statement" => {
            let Some(requested) = name else {
                return false;
            };
            let from_module = node
                .child_by_field_name("module_name")
                .map(text)
                .unwrap_or_default();
            if from_module != module {
                return false;
            }
            let mut cursor = node.walk();
            node.children_by_field_name("name", &mut cursor)
                .any(|child| match child.kind() {
                    "dotted_name" => text(child) == requested,
                    "aliased_import" => child
                        .child_by_field_name("name")
                        .is_some_and(|inner| text(inner) == requested),
                    _ => false,
                })
                || text(node).contains('*')
        }
        _ => false,
    }
}

fn named_children(node: Node<'_>) -> impl Iterator<Item = Node<'_>> {
    (0..u32::try_from(node.named_child_count()).unwrap_or(u32::MAX))
        .filter_map(move |index| node.named_child(index))
}

fn is_module_docstring(node: Node<'_>, saw_statement: bool) -> bool {
    // A string expression is the module docstring only when no real statement
    // precedes it; leading comments (including a shebang) do not count.
    !saw_statement
        && node.kind() == "expression_statement"
        && node
            .named_child(0)
            .is_some_and(|child| child.kind() == "string")
}

fn line_start_after(source: &str, byte: usize) -> usize {
    source[byte..]
        .find('\n')
        .map_or(source.len(), |offset| byte + offset + 1)
}

/// Plan a formatting-preserving import insertion.
///
/// Returns `Ok(None)` when the import already exists. The planned patch is a
/// pure insertion after the last top-level import (or after the module
/// docstring and leading comments when no import exists) and is returned only
/// after [`verify_patches`] certifies it.
pub fn plan_ensure_import(
    source: &str,
    module: &str,
    name: Option<&str>,
) -> Result<Option<SourcePatch>> {
    if !is_module_path(module) {
        bail!("module is not a dotted Python identifier path");
    }
    if let Some(name) = name
        && !is_python_identifier(name)
    {
        bail!("imported name is not a Python identifier");
    }
    let tree = parse_python_tree(source)?;
    let root = tree.root_node();
    if root.has_error() {
        bail!("cannot plan an import into source with syntax errors");
    }

    let mut last_import_end = None;
    let mut first_statement_start = None;
    let mut saw_statement = false;
    for child in named_children(root) {
        match child.kind() {
            "import_statement" | "import_from_statement" | "future_import_statement" => {
                if import_covers(child, source.as_bytes(), module, name) {
                    return Ok(None);
                }
                last_import_end = Some(child.end_byte());
                saw_statement = true;
            }
            "comment" => {}
            _ => {
                if first_statement_start.is_none() && !is_module_docstring(child, saw_statement) {
                    first_statement_start = Some(child.start_byte());
                }
                saw_statement = true;
            }
        }
    }

    let newline = if source.contains("\r\n") {
        "\r\n"
    } else {
        "\n"
    };
    let statement = match name {
        Some(name) => format!("from {module} import {name}"),
        None => format!("import {module}"),
    };
    validate_statement_snippet(&format!("{statement}\n"))?;

    let (offset, replacement) = match (last_import_end, first_statement_start) {
        (Some(end), _) => {
            let offset = line_start_after(source, end);
            if offset == source.len() && !source.ends_with('\n') {
                (offset, format!("{newline}{statement}{newline}"))
            } else {
                (offset, format!("{statement}{newline}"))
            }
        }
        (None, Some(start)) => (start, format!("{statement}{newline}{newline}")),
        (None, None) => {
            if source.is_empty() || source.ends_with('\n') {
                (source.len(), format!("{statement}{newline}"))
            } else {
                (source.len(), format!("{newline}{statement}{newline}"))
            }
        }
    };
    let patch = SourcePatch {
        start_byte: offset,
        end_byte: offset,
        replacement,
    };
    let verification = verify_patches(source, std::slice::from_ref(&patch))?;
    if !verification.certified {
        bail!(
            "planned import failed CST certification: {}",
            verification.diagnostics.join("; ")
        );
    }
    Ok(Some(patch))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundtrip_is_lossless_on_formatted_source() {
        let source = "import os\n\n\ndef main() -> int:  # entry\n    return os.EX_OK\n";
        let trip = verify_roundtrip(source);
        assert!(trip.parsed);
        assert!(trip.lossless);
        assert!(!trip.had_utf8_bom);
    }

    #[test]
    fn invalid_python_is_typed_data_not_an_error() {
        let trip = verify_roundtrip("def broken(:\n");
        assert!(!trip.parsed);
        assert!(!trip.lossless);
        assert!(trip.error.is_some());
    }

    #[test]
    fn patch_that_breaks_python_is_not_certified() {
        let source = "value = 1\n";
        let patch = SourcePatch {
            start_byte: 0,
            end_byte: 5,
            replacement: "def value(".to_owned(),
        };
        let verification = verify_patches(source, &[patch]).expect("verification");
        assert!(!verification.certified);
        assert!(!verification.postimage.parsed);
        assert!(
            verification
                .diagnostics
                .iter()
                .any(|entry| entry.contains("postimage"))
        );
    }

    #[test]
    fn overlapping_and_out_of_bounds_patches_are_rejected() {
        let source = "value = 1\n";
        let overlap = [
            SourcePatch {
                start_byte: 0,
                end_byte: 5,
                replacement: "x".to_owned(),
            },
            SourcePatch {
                start_byte: 4,
                end_byte: 6,
                replacement: "y".to_owned(),
            },
        ];
        assert!(verify_patches(source, &overlap).is_err());
        let outside = [SourcePatch {
            start_byte: 0,
            end_byte: source.len() + 1,
            replacement: "x".to_owned(),
        }];
        assert!(verify_patches(source, &outside).is_err());
        assert!(verify_patches(source, &[]).is_err());
    }

    #[test]
    fn ensure_import_rejects_code_injection_in_parameters() {
        let source = "import os\n";
        assert!(plan_ensure_import(source, "os; import sys", None).is_err());
        assert!(plan_ensure_import(source, "typing", Some("Any, eval")).is_err());
    }

    #[test]
    fn ensure_import_is_idempotent() {
        let source = "import os\nfrom typing import Any\n";
        assert!(
            plan_ensure_import(source, "os", None)
                .expect("plan")
                .is_none()
        );
        assert!(
            plan_ensure_import(source, "typing", Some("Any"))
                .expect("plan")
                .is_none()
        );
        let patch = plan_ensure_import(source, "typing", Some("Mapping"))
            .expect("plan")
            .expect("patch");
        let verification = verify_patches(source, &[patch]).expect("verification");
        assert!(verification.certified);
        assert!(
            verification
                .postimage_source
                .contains("from typing import Mapping\n")
        );
    }
}
