//! Real Oxc AST extraction: symbols, imports, exports, JSX component names,
//! and React hook usage, each with utf8-byte zero-based source ranges.
//!
//! Structural kinds reuse the tree-sitter node-kind vocabulary the fixed-kind
//! collector emitted, so envelope consumers see one vocabulary regardless of
//! which engine produced the range.

use oxc_ast::AstKind;
use oxc_ast::ast::{
    ClassType, Expression, FunctionType, JSXElementName, JSXMemberExpression,
    JSXMemberExpressionObject, Program, TSModuleDeclarationName, VariableDeclarationKind,
};
use oxc_ast_visit::Visit;
use oxc_span::Span;
use oxc_syntax::module_record::{
    ExportExportName, ExportImportName, ImportImportName, ModuleRecord,
};
use serde_json::json;

use crate::{MAX_SUMMARY_TOP_LEVEL, ModuleExport, ModuleImport, StructuralRange};

/// Byte-offset to zero-based row translation, built once per parse.
pub struct LineIndex {
    line_starts: Vec<usize>,
}

impl LineIndex {
    pub fn new(source: &str) -> Self {
        let mut line_starts = vec![0];
        for (offset, byte) in source.bytes().enumerate() {
            if byte == b'\n' {
                line_starts.push(offset + 1);
            }
        }
        Self { line_starts }
    }

    pub fn row(&self, byte: usize) -> usize {
        self.line_starts
            .partition_point(|start| *start <= byte)
            .saturating_sub(1)
    }
}

struct Collector<'index> {
    lines: &'index LineIndex,
    ranges: Vec<StructuralRange>,
}

impl Collector<'_> {
    fn push(&mut self, kind: &'static str, name: Option<String>, span: Span) {
        let start_byte = span.start as usize;
        let end_byte = span.end as usize;
        self.ranges.push(StructuralRange {
            kind: kind.into(),
            name,
            start_byte,
            end_byte,
            start_row: self.lines.row(start_byte),
            end_row: self.lines.row(end_byte),
        });
    }
}

impl<'a> Visit<'a> for Collector<'_> {
    fn enter_node(&mut self, kind: AstKind<'a>) {
        match kind {
            AstKind::Function(function) => {
                let name = function.id.as_ref().map(|id| id.name.to_string());
                match function.r#type {
                    FunctionType::FunctionDeclaration => {
                        let kind = if function.generator {
                            "generator_function_declaration"
                        } else {
                            "function_declaration"
                        };
                        self.push(kind, name, function.span);
                    }
                    FunctionType::TSDeclareFunction => {
                        self.push("function_signature", name, function.span);
                    }
                    FunctionType::FunctionExpression
                    | FunctionType::TSEmptyBodyFunctionExpression => {}
                }
            }
            AstKind::Class(class) => {
                if class.r#type == ClassType::ClassDeclaration {
                    let name = class.id.as_ref().map(|id| id.name.to_string());
                    self.push("class_declaration", name, class.span);
                }
            }
            AstKind::MethodDefinition(method) => {
                let name = method.key.static_name().map(std::borrow::Cow::into_owned);
                self.push("method_definition", name, method.span);
            }
            AstKind::VariableDeclaration(declaration) => {
                if !matches!(declaration.kind, VariableDeclarationKind::Var) {
                    self.push("lexical_declaration", None, declaration.span);
                }
            }
            AstKind::VariableDeclarator(declarator) => {
                let name = declarator
                    .id
                    .get_identifier_name()
                    .map(|name| name.to_string());
                self.push("variable_declarator", name, declarator.span);
            }
            AstKind::TSInterfaceDeclaration(interface) => {
                self.push(
                    "interface_declaration",
                    Some(interface.id.name.to_string()),
                    interface.span,
                );
            }
            AstKind::TSTypeAliasDeclaration(alias) => {
                self.push(
                    "type_alias_declaration",
                    Some(alias.id.name.to_string()),
                    alias.span,
                );
            }
            AstKind::TSEnumDeclaration(declaration) => {
                self.push(
                    "enum_declaration",
                    Some(declaration.id.name.to_string()),
                    declaration.span,
                );
            }
            AstKind::TSModuleDeclaration(module) => {
                let name = match &module.id {
                    TSModuleDeclarationName::Identifier(identifier) => identifier.name.to_string(),
                    TSModuleDeclarationName::StringLiteral(literal) => literal.value.to_string(),
                };
                self.push("internal_module", Some(name), module.span);
            }
            AstKind::ImportDeclaration(declaration) => {
                self.push("import_statement", None, declaration.span);
            }
            AstKind::ExportNamedDeclaration(declaration) => {
                self.push("export_statement", None, declaration.span);
            }
            AstKind::ExportDefaultDeclaration(declaration) => {
                self.push("export_statement", None, declaration.span);
            }
            AstKind::ExportAllDeclaration(declaration) => {
                self.push("export_statement", None, declaration.span);
            }
            AstKind::JSXOpeningElement(element) => {
                if let Some(name) = jsx_component_name(&element.name) {
                    self.push("jsx_component", Some(name), element.span);
                }
            }
            AstKind::CallExpression(call) => {
                if let Some(name) = react_hook_name(&call.callee) {
                    self.push("react_hook", Some(name), call.span);
                }
            }
            _ => {}
        }
    }
}

/// Walk the parsed program and return structural ranges in pre-order
/// (start ascending, containing range first).
pub fn collect_structure(program: &Program<'_>, lines: &LineIndex) -> Vec<StructuralRange> {
    let mut collector = Collector {
        lines,
        ranges: Vec::new(),
    };
    collector.visit_program(program);
    collector.ranges.sort_by(|left, right| {
        left.start_byte
            .cmp(&right.start_byte)
            .then(right.end_byte.cmp(&left.end_byte))
            .then(left.kind.cmp(&right.kind))
    });
    collector.ranges
}

fn jsx_component_name(name: &JSXElementName<'_>) -> Option<String> {
    match name {
        // Lowercase identifiers are host elements; the parser classifies
        // component references as `IdentifierReference`.
        JSXElementName::Identifier(identifier) => {
            let name = identifier.name.to_string();
            name.chars()
                .next()
                .is_some_and(|first| first.is_ascii_uppercase())
                .then_some(name)
        }
        JSXElementName::IdentifierReference(identifier) => Some(identifier.name.to_string()),
        JSXElementName::MemberExpression(member) => Some(jsx_member_name(member)),
        JSXElementName::NamespacedName(_) | JSXElementName::ThisExpression(_) => None,
    }
}

fn jsx_member_name(member: &JSXMemberExpression<'_>) -> String {
    let mut parts = vec![member.property.name.to_string()];
    let mut object = &member.object;
    loop {
        match object {
            JSXMemberExpressionObject::IdentifierReference(identifier) => {
                parts.push(identifier.name.to_string());
                break;
            }
            JSXMemberExpressionObject::MemberExpression(inner) => {
                parts.push(inner.property.name.to_string());
                object = &inner.object;
            }
            JSXMemberExpressionObject::ThisExpression(_) => {
                parts.push("this".to_owned());
                break;
            }
        }
    }
    parts.reverse();
    parts.join(".")
}

fn react_hook_name(callee: &Expression<'_>) -> Option<String> {
    let name = match callee {
        Expression::Identifier(identifier) => identifier.name.as_str(),
        Expression::StaticMemberExpression(member) => member.property.name.as_str(),
        _ => return None,
    };
    is_hook_name(name).then(|| name.to_owned())
}

fn is_hook_name(name: &str) -> bool {
    name == "use"
        || (name.starts_with("use") && name.as_bytes().get(3).is_some_and(u8::is_ascii_uppercase))
}

/// Flatten the parser's ECMAScript module record into typed import edges.
pub fn module_imports(record: &ModuleRecord<'_>, source: &str) -> Vec<ModuleImport> {
    let mut imports = Vec::new();
    for entry in &record.import_entries {
        let (kind, imported_name) = match &entry.import_name {
            ImportImportName::Name(name) => ("named", Some(name.name.to_string())),
            ImportImportName::NamespaceObject => ("namespace", None),
            ImportImportName::Default(_) => ("default", None),
        };
        imports.push(ModuleImport {
            module_request: entry.module_request.name.to_string(),
            imported_name,
            local_name: Some(entry.local_name.name.to_string()),
            kind: kind.into(),
            is_type: entry.is_type,
            start_byte: entry.statement_span.start as usize,
            end_byte: entry.statement_span.end as usize,
        });
    }
    for (specifier, occurrences) in &record.requested_modules {
        for occurrence in occurrences {
            let covered = record
                .import_entries
                .iter()
                .any(|entry| entry.statement_span == occurrence.statement_span);
            if occurrence.is_import && !covered {
                imports.push(ModuleImport {
                    module_request: specifier.to_string(),
                    imported_name: None,
                    local_name: None,
                    kind: "side_effect".into(),
                    is_type: occurrence.is_type,
                    start_byte: occurrence.statement_span.start as usize,
                    end_byte: occurrence.statement_span.end as usize,
                });
            }
        }
    }
    for dynamic in &record.dynamic_imports {
        let request = source
            .get(dynamic.module_request.start as usize..dynamic.module_request.end as usize)
            .unwrap_or_default();
        imports.push(ModuleImport {
            module_request: trim_string_literal(request).to_owned(),
            imported_name: None,
            local_name: None,
            kind: "dynamic".into(),
            is_type: false,
            start_byte: dynamic.span.start as usize,
            end_byte: dynamic.span.end as usize,
        });
    }
    imports.sort_by(|left, right| {
        (left.start_byte, left.end_byte, &left.module_request).cmp(&(
            right.start_byte,
            right.end_byte,
            &right.module_request,
        ))
    });
    imports
}

/// Flatten the parser's ECMAScript module record into typed export edges.
pub fn module_exports(record: &ModuleRecord<'_>) -> Vec<ModuleExport> {
    let mut exports = Vec::new();
    for entry in &record.local_export_entries {
        let kind = if entry.export_name.is_default() || entry.local_name.is_default() {
            "default"
        } else {
            "named"
        };
        exports.push(ModuleExport {
            export_name: export_name_text(&entry.export_name),
            local_name: entry.local_name.name().map(|name| name.to_string()),
            imported_name: None,
            module_request: None,
            kind: kind.into(),
            is_type: entry.is_type,
            start_byte: entry.span.start as usize,
            end_byte: entry.span.end as usize,
        });
    }
    for entry in &record.indirect_export_entries {
        exports.push(ModuleExport {
            export_name: export_name_text(&entry.export_name),
            local_name: entry.local_name.name().map(|name| name.to_string()),
            imported_name: match &entry.import_name {
                ExportImportName::Name(name) => Some(name.name.to_string()),
                ExportImportName::All | ExportImportName::AllButDefault => Some("*".into()),
                ExportImportName::Null => None,
            },
            module_request: entry
                .module_request
                .as_ref()
                .map(|request| request.name.to_string()),
            kind: "re_export".into(),
            is_type: entry.is_type,
            start_byte: entry.span.start as usize,
            end_byte: entry.span.end as usize,
        });
    }
    for entry in &record.star_export_entries {
        exports.push(ModuleExport {
            export_name: export_name_text(&entry.export_name),
            local_name: None,
            imported_name: Some("*".into()),
            module_request: entry
                .module_request
                .as_ref()
                .map(|request| request.name.to_string()),
            kind: "star".into(),
            is_type: entry.is_type,
            start_byte: entry.span.start as usize,
            end_byte: entry.span.end as usize,
        });
    }
    exports.sort_by(|left, right| {
        (left.start_byte, left.end_byte, &left.export_name).cmp(&(
            right.start_byte,
            right.end_byte,
            &right.export_name,
        ))
    });
    exports
}

fn export_name_text(name: &ExportExportName<'_>) -> Option<String> {
    match name {
        ExportExportName::Name(name) => Some(name.name.to_string()),
        ExportExportName::Default(_) => Some("default".into()),
        ExportExportName::Null => None,
    }
}

fn trim_string_literal(raw: &str) -> &str {
    let bytes = raw.as_bytes();
    if bytes.len() >= 2
        && (bytes[0] == b'"' || bytes[0] == b'\'')
        && bytes[bytes.len() - 1] == bytes[0]
    {
        &raw[1..raw.len() - 1]
    } else {
        raw
    }
}

/// Compact, real program summary: counts plus the top-level extracted items,
/// capped at [`MAX_SUMMARY_TOP_LEVEL`].
pub fn program_summary(
    source_type: &str,
    statement_count: usize,
    panicked: bool,
    ranges: &[StructuralRange],
    imports: &[ModuleImport],
    exports: &[ModuleExport],
) -> serde_json::Value {
    let jsx_components = ranges
        .iter()
        .filter(|range| range.kind == "jsx_component")
        .count();
    let react_hooks = ranges
        .iter()
        .filter(|range| range.kind == "react_hook")
        .count();
    let mut top_level = Vec::new();
    let mut truncated = false;
    let mut open_end = 0usize;
    for range in ranges {
        if range.kind == "program" || range.start_byte < open_end {
            continue;
        }
        open_end = range.end_byte;
        if top_level.len() >= MAX_SUMMARY_TOP_LEVEL {
            truncated = true;
            break;
        }
        top_level.push(json!({
            "kind": range.kind,
            "name": range.name,
            "startByte": range.start_byte,
            "endByte": range.end_byte,
        }));
    }
    json!({
        "kind": "Program",
        "sourceType": source_type,
        "panicked": panicked,
        "counts": {
            "statements": statement_count,
            "structuralRanges": ranges.len(),
            "imports": imports.len(),
            "exports": exports.len(),
            "jsxComponents": jsx_components,
            "reactHooks": react_hooks,
        },
        "topLevel": top_level,
        "topLevelTruncated": truncated,
    })
}
