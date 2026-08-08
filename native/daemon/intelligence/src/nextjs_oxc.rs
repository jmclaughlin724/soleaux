//! Real Oxc AST analysis for Next.js source files: HTTP-method exports in
//! route handlers, route-segment config exports, Server Actions, and boundary
//! facts, each with utf8-byte zero-based spans taken from the parsed program.
//!
//! When the Oxc parser panics on a file, analysis degrades to the previous
//! textual scan and labels itself [`ENGINE_DEGRADED_TEXTUAL`] so consumers can
//! distinguish verified AST facts from a recovery pass. The module also owns
//! the per-application Next.js version gate against the embedded P5-010 matrix
//! and cross-application route reconciliation for multi-app workspaces.

use crate::nextjs::NextRoute;
use crate::turbo_next_matrix::TURBO_NEXT_MATRIX_JSON;
use crate::turborepo::pnpm_catalog_pin;
use oxc_allocator::Allocator;
use oxc_ast::AstKind;
use oxc_ast::ast::{
    Declaration, Directive, ExportDefaultDeclarationKind, Expression, Program, Statement,
};
use oxc_ast_visit::Visit;
use oxc_parser::Parser as OxcParser;
use oxc_span::{SourceType, Span};
use oxc_syntax::module_record::{ExportExportName, ModuleRecord};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;
use std::fs;
use std::path::Path;

pub const HTTP_METHODS: [&str; 7] = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"];

/// Route-segment config and metadata constants recognized on app-router files.
pub const SEGMENT_CONFIG_CONSTS: [&str; 9] = [
    "dynamic",
    "dynamicParams",
    "revalidate",
    "fetchCache",
    "runtime",
    "preferredRegion",
    "maxDuration",
    "metadata",
    "viewport",
];

/// Generator-function exports recognized on app-router files.
pub const SEGMENT_CONFIG_FUNCTIONS: [&str; 5] = [
    "generateStaticParams",
    "generateMetadata",
    "generateViewport",
    "generateSitemaps",
    "generateImageMetadata",
];

/// App-router boundary file stems, excluding `page` and `route`.
pub const BOUNDARY_STEMS: [&str; 7] = [
    "layout",
    "template",
    "loading",
    "error",
    "global-error",
    "not-found",
    "default",
];

pub const ENGINE_OXC_AST: &str = "oxc-ast";
pub const ENGINE_DEGRADED_TEXTUAL: &str = "degraded-textual-scan";

pub const VERSION_GATE_FULL: &str = "full";
pub const VERSION_GATE_SAFE: &str = "safe";

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct MethodExport {
    pub method: String,
    /// One of `declaration`, `specifier`, `re_export`, `textual`.
    pub export_kind: String,
    pub start_byte: usize,
    pub end_byte: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct SegmentConfigExport {
    pub name: String,
    /// One of `const`, `function`.
    pub kind: String,
    /// Source text of a string, number, or boolean literal initializer.
    pub value: Option<String>,
    pub start_byte: usize,
    pub end_byte: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ExtractedAction {
    /// `None` for an anonymous inline action.
    pub name: Option<String>,
    /// `module` for a `"use server"` file export, `function` for an inline
    /// `"use server"` function body.
    pub scope: String,
    pub exported: bool,
    pub start_byte: usize,
    pub end_byte: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct DefaultExportFact {
    pub name: Option<String>,
    pub start_byte: usize,
    pub end_byte: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct RouteFileAnalysis {
    /// [`ENGINE_OXC_AST`] or [`ENGINE_DEGRADED_TEXTUAL`].
    pub engine: String,
    pub module_directives: Vec<String>,
    pub methods: Vec<MethodExport>,
    pub segment_config: Vec<SegmentConfigExport>,
    pub server_actions: Vec<ExtractedAction>,
    pub default_export: Option<DefaultExportFact>,
    pub parse_error_count: usize,
}

pub fn analyze_route_file(relative_path: &str, source: &str) -> RouteFileAnalysis {
    let source_type = SourceType::from_path(Path::new(relative_path)).unwrap_or_default();
    if !(source_type.is_javascript() || source_type.is_typescript()) {
        return textual_analysis(source, 0);
    }
    let allocator = Allocator::default();
    let parsed = OxcParser::new(&allocator, source, source_type).parse();
    if parsed.panicked {
        return textual_analysis(source, parsed.diagnostics.len());
    }
    ast_analysis(
        &parsed.program,
        &parsed.module_record,
        parsed.diagnostics.len(),
    )
}

fn ast_analysis(
    program: &Program<'_>,
    record: &ModuleRecord<'_>,
    parse_error_count: usize,
) -> RouteFileAnalysis {
    let module_directives = directive_names(&program.directives);
    let mut methods = Vec::new();
    let mut segment_config = Vec::new();
    let mut function_bindings = BTreeMap::<String, Span>::new();
    let mut default_export: Option<DefaultExportFact> = None;
    let mut default_function: Option<(Option<String>, Span)> = None;

    for statement in &program.body {
        match statement {
            Statement::FunctionDeclaration(function) => {
                if let Some(id) = &function.id {
                    function_bindings.insert(id.name.to_string(), function.span);
                }
            }
            Statement::VariableDeclaration(declaration) => {
                collect_function_declarators(declaration, &mut function_bindings);
            }
            Statement::ExportNamedDeclaration(export) => match &export.declaration {
                Some(Declaration::FunctionDeclaration(function)) => {
                    if let Some(id) = &function.id {
                        function_bindings.insert(id.name.to_string(), function.span);
                        if HTTP_METHODS.contains(&id.name.as_str()) {
                            methods.push(MethodExport {
                                method: id.name.to_string(),
                                export_kind: "declaration".into(),
                                start_byte: function.span.start as usize,
                                end_byte: function.span.end as usize,
                            });
                        }
                        if SEGMENT_CONFIG_FUNCTIONS.contains(&id.name.as_str()) {
                            segment_config.push(SegmentConfigExport {
                                name: id.name.to_string(),
                                kind: "function".into(),
                                value: None,
                                start_byte: function.span.start as usize,
                                end_byte: function.span.end as usize,
                            });
                        }
                    }
                }
                Some(Declaration::VariableDeclaration(declaration)) => {
                    collect_function_declarators(declaration, &mut function_bindings);
                    for declarator in &declaration.declarations {
                        let Some(name) = declarator.id.get_identifier_name() else {
                            continue;
                        };
                        if HTTP_METHODS.contains(&name.as_str()) {
                            methods.push(MethodExport {
                                method: name.to_string(),
                                export_kind: "declaration".into(),
                                start_byte: declarator.span.start as usize,
                                end_byte: declarator.span.end as usize,
                            });
                        }
                        if SEGMENT_CONFIG_CONSTS.contains(&name.as_str()) {
                            segment_config.push(SegmentConfigExport {
                                name: name.to_string(),
                                kind: "const".into(),
                                value: declarator.init.as_ref().and_then(literal_text),
                                start_byte: declarator.span.start as usize,
                                end_byte: declarator.span.end as usize,
                            });
                        }
                    }
                }
                _ => {}
            },
            Statement::ExportDefaultDeclaration(export) => {
                let name = match &export.declaration {
                    ExportDefaultDeclarationKind::FunctionDeclaration(function) => {
                        let name = function.id.as_ref().map(|id| id.name.to_string());
                        default_function = Some((name.clone(), export.span));
                        name
                    }
                    ExportDefaultDeclarationKind::ClassDeclaration(class) => {
                        class.id.as_ref().map(|id| id.name.to_string())
                    }
                    ExportDefaultDeclarationKind::ArrowFunctionExpression(_) => {
                        default_function = Some((None, export.span));
                        None
                    }
                    ExportDefaultDeclarationKind::Identifier(identifier) => {
                        Some(identifier.name.to_string())
                    }
                    _ => None,
                };
                default_export = Some(DefaultExportFact {
                    name,
                    start_byte: export.span.start as usize,
                    end_byte: export.span.end as usize,
                });
            }
            _ => {}
        }
    }

    // Specifier exports (`export { GET }`, `export { handler as POST }`) and
    // `export { X as default }` reach the module record without a declaration.
    for entry in &record.local_export_entries {
        if entry.is_type {
            continue;
        }
        if entry.export_name.is_default() || entry.local_name.is_default() {
            if default_export.is_none() {
                default_export = Some(DefaultExportFact {
                    name: entry.local_name.name().map(|name| name.to_string()),
                    start_byte: entry.span.start as usize,
                    end_byte: entry.span.end as usize,
                });
            }
            continue;
        }
        let Some(export_name) = export_name_text(&entry.export_name) else {
            continue;
        };
        if HTTP_METHODS.contains(&export_name.as_str())
            && !methods.iter().any(|method| method.method == export_name)
        {
            methods.push(MethodExport {
                method: export_name,
                export_kind: "specifier".into(),
                start_byte: entry.span.start as usize,
                end_byte: entry.span.end as usize,
            });
        }
    }
    for entry in &record.indirect_export_entries {
        if entry.is_type {
            continue;
        }
        let Some(export_name) = export_name_text(&entry.export_name) else {
            continue;
        };
        if HTTP_METHODS.contains(&export_name.as_str())
            && !methods.iter().any(|method| method.method == export_name)
        {
            methods.push(MethodExport {
                method: export_name,
                export_kind: "re_export".into(),
                start_byte: entry.span.start as usize,
                end_byte: entry.span.end as usize,
            });
        }
    }

    let mut server_actions = Vec::new();
    if module_directives.iter().any(|name| name == "use server") {
        for entry in &record.local_export_entries {
            if entry.is_type {
                continue;
            }
            if entry.export_name.is_default() || entry.local_name.is_default() {
                if let Some((name, span)) = &default_function {
                    server_actions.push(ExtractedAction {
                        name: name.clone().or_else(|| Some("default".into())),
                        scope: "module".into(),
                        exported: true,
                        start_byte: span.start as usize,
                        end_byte: span.end as usize,
                    });
                }
                continue;
            }
            let Some(export_name) = export_name_text(&entry.export_name) else {
                continue;
            };
            let local = entry
                .local_name
                .name()
                .map(|name| name.to_string())
                .unwrap_or_else(|| export_name.clone());
            if let Some(span) = function_bindings.get(&local) {
                server_actions.push(ExtractedAction {
                    name: Some(export_name),
                    scope: "module".into(),
                    exported: true,
                    start_byte: span.start as usize,
                    end_byte: span.end as usize,
                });
            }
        }
    }
    let mut collector = InlineActionCollector {
        declarator_names: Vec::new(),
        actions: Vec::new(),
    };
    collector.visit_program(program);
    for (name, span) in collector.actions {
        let exported = name.as_deref().is_some_and(|name| {
            record
                .exported_bindings
                .iter()
                .any(|(binding, _)| binding.as_str() == name)
        });
        let action = ExtractedAction {
            name,
            scope: "function".into(),
            exported,
            start_byte: span.start as usize,
            end_byte: span.end as usize,
        };
        if !server_actions.iter().any(|existing| {
            existing.name == action.name && existing.start_byte == action.start_byte
        }) {
            server_actions.push(action);
        }
    }

    methods.sort_by(|left, right| {
        (left.start_byte, &left.method).cmp(&(right.start_byte, &right.method))
    });
    segment_config
        .sort_by(|left, right| (left.start_byte, &left.name).cmp(&(right.start_byte, &right.name)));
    server_actions
        .sort_by(|left, right| (left.start_byte, &left.name).cmp(&(right.start_byte, &right.name)));
    server_actions
        .dedup_by(|left, right| left.name == right.name && left.start_byte == right.start_byte);

    RouteFileAnalysis {
        engine: ENGINE_OXC_AST.into(),
        module_directives,
        methods,
        segment_config,
        server_actions,
        default_export,
        parse_error_count,
    }
}

struct InlineActionCollector {
    declarator_names: Vec<Option<String>>,
    actions: Vec<(Option<String>, Span)>,
}

impl<'a> Visit<'a> for InlineActionCollector {
    fn enter_node(&mut self, kind: AstKind<'a>) {
        match kind {
            AstKind::VariableDeclarator(declarator) => {
                self.declarator_names.push(
                    declarator
                        .id
                        .get_identifier_name()
                        .map(|name| name.to_string()),
                );
            }
            AstKind::Function(function) => {
                if let Some(body) = &function.body
                    && has_use_server(&body.directives)
                {
                    let name = function
                        .id
                        .as_ref()
                        .map(|id| id.name.to_string())
                        .or_else(|| self.declarator_names.last().cloned().flatten());
                    self.actions.push((name, function.span));
                }
            }
            AstKind::ArrowFunctionExpression(arrow)
                if !arrow.expression && has_use_server(&arrow.body.directives) =>
            {
                let name = self.declarator_names.last().cloned().flatten();
                self.actions.push((name, arrow.span));
            }
            _ => {}
        }
    }

    fn leave_node(&mut self, kind: AstKind<'a>) {
        if matches!(kind, AstKind::VariableDeclarator(_)) {
            self.declarator_names.pop();
        }
    }
}

fn collect_function_declarators(
    declaration: &oxc_ast::ast::VariableDeclaration<'_>,
    bindings: &mut BTreeMap<String, Span>,
) {
    for declarator in &declaration.declarations {
        if let Some(name) = declarator.id.get_identifier_name()
            && matches!(
                declarator.init,
                Some(Expression::ArrowFunctionExpression(_) | Expression::FunctionExpression(_))
            )
        {
            bindings.insert(name.to_string(), declarator.span);
        }
    }
}

fn directive_names(directives: &[Directive<'_>]) -> Vec<String> {
    let mut names = Vec::new();
    for directive in directives {
        let value = directive.expression.value.as_str();
        if (value == "use server" || value == "use client")
            && !names.iter().any(|existing| existing == value)
        {
            names.push(value.to_string());
        }
    }
    names
}

fn has_use_server(directives: &[Directive<'_>]) -> bool {
    directives
        .iter()
        .any(|directive| directive.expression.value.as_str() == "use server")
}

fn export_name_text(name: &ExportExportName<'_>) -> Option<String> {
    match name {
        ExportExportName::Name(name) => Some(name.name.to_string()),
        ExportExportName::Default(_) => Some("default".into()),
        ExportExportName::Null => None,
    }
}

fn literal_text(expression: &Expression<'_>) -> Option<String> {
    match expression {
        Expression::StringLiteral(literal) => Some(literal.value.to_string()),
        Expression::NumericLiteral(literal) => Some(
            literal
                .raw
                .map(|raw| raw.to_string())
                .unwrap_or_else(|| literal.value.to_string()),
        ),
        Expression::BooleanLiteral(literal) => Some(literal.value.to_string()),
        _ => None,
    }
}

/// The previous string-matching detection, kept as the labeled recovery path
/// for sources the Oxc parser cannot produce an AST for.
fn textual_analysis(source: &str, parse_error_count: usize) -> RouteFileAnalysis {
    let mut methods = Vec::new();
    for method in HTTP_METHODS {
        let patterns = [
            format!("export async function {method}"),
            format!("export function {method}"),
            format!("export const {method}"),
        ];
        if let Some((offset, length)) = patterns
            .iter()
            .find_map(|pattern| source.find(pattern).map(|offset| (offset, pattern.len())))
        {
            methods.push(MethodExport {
                method: method.to_string(),
                export_kind: "textual".into(),
                start_byte: offset,
                end_byte: offset + length,
            });
        }
    }
    methods.sort_by(|left, right| {
        (left.start_byte, &left.method).cmp(&(right.start_byte, &right.method))
    });
    let has_use_server = ["use server", "'use server'", "\"use server\""]
        .iter()
        .any(|needle| source.contains(needle));
    let mut server_actions = Vec::new();
    if has_use_server {
        let mut offset = 0usize;
        for line in source.split_inclusive('\n') {
            let trimmed = line.trim_start();
            let indent = line.len() - trimmed.len();
            for prefix in [
                "export async function ",
                "export function ",
                "export const ",
            ] {
                if let Some(rest) = trimmed.strip_prefix(prefix) {
                    let name = rest
                        .split(|character: char| {
                            !(character.is_alphanumeric() || character == '_' || character == '$')
                        })
                        .next()
                        .unwrap_or_default();
                    if !name.is_empty()
                        && !server_actions
                            .iter()
                            .any(|action: &ExtractedAction| action.name.as_deref() == Some(name))
                    {
                        let start = offset + indent;
                        server_actions.push(ExtractedAction {
                            name: Some(name.to_string()),
                            scope: "module".into(),
                            exported: true,
                            start_byte: start,
                            end_byte: start + prefix.len() + name.len(),
                        });
                    }
                }
            }
            offset += line.len();
        }
    }
    let default_export = source
        .find("export default")
        .map(|offset| DefaultExportFact {
            name: None,
            start_byte: offset,
            end_byte: offset + "export default".len(),
        });
    let mut module_directives = Vec::new();
    if has_use_server {
        module_directives.push("use server".to_string());
    }
    if source.contains("'use client'") || source.contains("\"use client\"") {
        module_directives.push("use client".to_string());
    }
    RouteFileAnalysis {
        engine: ENGINE_DEGRADED_TEXTUAL.into(),
        module_directives,
        methods,
        segment_config: Vec::new(),
        server_actions,
        default_export,
        parse_error_count,
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct NextVersionGate {
    pub app_root: String,
    /// The `next` range as declared by the application manifest.
    pub declared: Option<String>,
    /// The declared value with `catalog:` references resolved against the
    /// nearest `pnpm-workspace.yaml` default catalog.
    pub resolved: Option<String>,
    /// `true` when the resolved version is pinned by the embedded matrix.
    pub pinned: bool,
    /// [`VERSION_GATE_FULL`] or [`VERSION_GATE_SAFE`].
    pub mode: String,
    pub reason: Option<String>,
}

/// Next.js versions pinned by the embedded P5-010 matrix contract.
pub fn matrix_next_versions() -> Vec<String> {
    let matrix: Value = serde_json::from_str(TURBO_NEXT_MATRIX_JSON)
        .expect("the embedded turbo-next matrix is schema-checked at build time");
    matrix
        .get("tools")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter(|tool| tool.get("id").and_then(Value::as_str) == Some("nextjs"))
        .filter_map(|tool| tool.get("versions").and_then(Value::as_array))
        .flatten()
        .filter_map(|version| version.get("version").and_then(Value::as_str))
        .map(str::to_string)
        .collect()
}

/// Version-gate one application directory against the matrix pins. Unknown or
/// unresolvable versions degrade to [`VERSION_GATE_SAFE`] with a reason.
pub fn next_version_gate(application_dir: &Path, app_root: &str) -> NextVersionGate {
    let declared = read_declared_next(application_dir);
    let Some(declared) = declared else {
        return NextVersionGate {
            app_root: app_root.to_string(),
            declared: None,
            resolved: None,
            pinned: false,
            mode: VERSION_GATE_SAFE.into(),
            reason: Some("the application manifest declares no next dependency".into()),
        };
    };
    let (resolved, unresolved_reason) = if declared == "catalog:" {
        match nearest_pnpm_workspace(application_dir)
            .and_then(|content| pnpm_catalog_pin(&content, "next"))
        {
            Some(pin) => (Some(pin), None),
            None => (
                None,
                Some(
                    "the catalog: reference did not resolve against a pnpm-workspace.yaml default catalog"
                        .to_string(),
                ),
            ),
        }
    } else if declared.starts_with("catalog:") || declared.starts_with("workspace:") {
        (
            None,
            Some(format!(
                "the {declared} dependency protocol is not resolvable without a package manager"
            )),
        )
    } else {
        (Some(declared.clone()), None)
    };
    let pins = matrix_next_versions();
    let pinned = resolved
        .as_deref()
        .is_some_and(|version| pins.iter().any(|pin| pin == version));
    let reason = if pinned {
        None
    } else if let Some(reason) = unresolved_reason {
        Some(reason)
    } else {
        resolved.as_deref().map(|version| {
            format!("next {version} is not pinned by the embedded turbo-next matrix")
        })
    };
    NextVersionGate {
        app_root: app_root.to_string(),
        declared: Some(declared),
        resolved,
        pinned,
        mode: if pinned {
            VERSION_GATE_FULL.into()
        } else {
            VERSION_GATE_SAFE.into()
        },
        reason,
    }
}

fn read_declared_next(application_dir: &Path) -> Option<String> {
    let manifest: Value =
        serde_json::from_slice(&fs::read(application_dir.join("package.json")).ok()?).ok()?;
    ["dependencies", "devDependencies"]
        .iter()
        .find_map(|section| {
            manifest
                .get(section)?
                .get("next")?
                .as_str()
                .map(str::to_string)
        })
}

fn nearest_pnpm_workspace(application_dir: &Path) -> Option<String> {
    let mut current = Some(application_dir);
    for _ in 0..8 {
        let directory = current?;
        let candidate = directory.join("pnpm-workspace.yaml");
        if candidate.is_file() {
            return fs::read_to_string(candidate).ok();
        }
        current = directory.parent();
    }
    None
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct CrossAppRoute {
    pub route: String,
    pub apps: Vec<String>,
    pub files: Vec<String>,
    /// `conflict` when more than one application claims the route path,
    /// `unique` otherwise.
    pub status: String,
}

/// Reconcile the merged multi-application route list: group by route path and
/// mark paths claimed by more than one application as conflicts.
pub fn reconcile_cross_app(routes: &[NextRoute]) -> Vec<CrossAppRoute> {
    let mut grouped = BTreeMap::<&str, (Vec<String>, Vec<String>)>::new();
    for route in routes {
        let (apps, files) = grouped.entry(route.route.as_str()).or_default();
        if !apps.contains(&route.app_root) {
            apps.push(route.app_root.clone());
        }
        if !files.contains(&route.file) {
            files.push(route.file.clone());
        }
    }
    grouped
        .into_iter()
        .map(|(route, (mut apps, mut files))| {
            apps.sort();
            files.sort();
            let status = if apps.len() > 1 { "conflict" } else { "unique" };
            CrossAppRoute {
                route: route.to_string(),
                apps,
                files,
                status: status.to_string(),
            }
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn route_handler_method_exports_carry_exact_spans() {
        let source = r#"import { HEAD } from "./shared";
// export function DELETE would be a comment, not a handler
const note = "export const GET inside a string";
export const GETTER = 1;
export type { OPTIONS } from "./types";
export async function GET(request: Request) {
  return new Response("ok");
}
export const POST = async () => new Response(null, { status: 201 });
function remove() {}
export { remove as DELETE };
export { HEAD };
export { PUT } from "./shared";
"#;
        let analysis = analyze_route_file("app/api/items/route.ts", source);
        assert_eq!(analysis.engine, ENGINE_OXC_AST);
        let methods = analysis
            .methods
            .iter()
            .map(|method| method.method.as_str())
            .collect::<Vec<_>>();
        assert_eq!(methods, vec!["GET", "POST", "DELETE", "HEAD", "PUT"]);
        let get = analysis
            .methods
            .iter()
            .find(|method| method.method == "GET")
            .expect("GET");
        assert_eq!(get.export_kind, "declaration");
        assert!(
            source[get.start_byte..get.end_byte].starts_with("async function GET"),
            "unexpected GET span text: {:?}",
            &source[get.start_byte..get.end_byte]
        );
        let post = analysis
            .methods
            .iter()
            .find(|method| method.method == "POST")
            .expect("POST");
        assert!(
            source[post.start_byte..post.end_byte].starts_with("POST = async"),
            "unexpected POST span text: {:?}",
            &source[post.start_byte..post.end_byte]
        );
        let put = analysis
            .methods
            .iter()
            .find(|method| method.method == "PUT")
            .expect("PUT");
        assert_eq!(put.export_kind, "re_export");
        assert!(
            !methods.contains(&"OPTIONS"),
            "type-only export is not a handler"
        );
    }

    #[test]
    fn segment_config_exports_carry_literal_values_and_spans() {
        let source = r#"export const dynamic = "force-static";
export const revalidate = 60;
export const dynamicParams = false;
export const runtime = "edge";
export async function generateStaticParams() {
  return [];
}
export default function Page() {
  return null;
}
"#;
        let analysis = analyze_route_file("app/blog/[slug]/page.tsx", source);
        assert_eq!(analysis.engine, ENGINE_OXC_AST);
        let by_name = |name: &str| {
            analysis
                .segment_config
                .iter()
                .find(|config| config.name == name)
                .unwrap_or_else(|| panic!("missing segment config {name}"))
        };
        assert_eq!(by_name("dynamic").value.as_deref(), Some("force-static"));
        assert_eq!(by_name("revalidate").value.as_deref(), Some("60"));
        assert_eq!(by_name("dynamicParams").value.as_deref(), Some("false"));
        assert_eq!(by_name("runtime").value.as_deref(), Some("edge"));
        let generate = by_name("generateStaticParams");
        assert_eq!(generate.kind, "function");
        assert!(
            source[generate.start_byte..generate.end_byte]
                .starts_with("async function generateStaticParams"),
        );
        let dynamic = by_name("dynamic");
        assert_eq!(
            &source[dynamic.start_byte..dynamic.end_byte],
            "dynamic = \"force-static\""
        );
        let default = analysis.default_export.expect("default export");
        assert_eq!(default.name.as_deref(), Some("Page"));
        assert!(source[default.start_byte..default.end_byte].starts_with("export default"));
    }

    #[test]
    fn server_actions_are_extracted_at_module_and_function_scope() {
        let module_level = r#""use server";
export async function saveUser(data: FormData) {}
export const removeUser = async (id: string) => {};
export const runtime = "nodejs";
async function hidden() {}
export { hidden as archiveUser };
"#;
        let analysis = analyze_route_file("app/actions.ts", module_level);
        assert_eq!(analysis.engine, ENGINE_OXC_AST);
        assert_eq!(analysis.module_directives, vec!["use server"]);
        let names = analysis
            .server_actions
            .iter()
            .map(|action| action.name.as_deref().unwrap_or_default())
            .collect::<Vec<_>>();
        assert_eq!(names, vec!["saveUser", "removeUser", "archiveUser"]);
        assert!(
            !names.contains(&"runtime"),
            "a non-function export is not a server action"
        );
        let save = &analysis.server_actions[0];
        assert!(
            module_level[save.start_byte..save.end_byte].starts_with("async function saveUser")
        );
        assert!(save.exported);
        assert_eq!(save.scope, "module");

        let function_level = r#"export default function Page() {
  const submit = async (data: FormData) => {
    "use server";
    return data;
  };
  async function persist() {
    "use server";
  }
  return null;
}
export async function annotate() {
  "use server";
}
"#;
        let analysis = analyze_route_file("app/settings/page.tsx", function_level);
        let mut names = analysis
            .server_actions
            .iter()
            .map(|action| {
                (
                    action.name.as_deref().unwrap_or_default().to_string(),
                    action.scope.clone(),
                    action.exported,
                )
            })
            .collect::<Vec<_>>();
        names.sort();
        assert_eq!(
            names,
            vec![
                ("annotate".to_string(), "function".to_string(), true),
                ("persist".to_string(), "function".to_string(), false),
                ("submit".to_string(), "function".to_string(), false),
            ]
        );
        let submit = analysis
            .server_actions
            .iter()
            .find(|action| action.name.as_deref() == Some("submit"))
            .expect("submit");
        assert!(function_level[submit.start_byte..submit.end_byte].starts_with("async (data"));
    }

    #[test]
    fn boundary_files_report_default_export_and_use_client() {
        let error_boundary = r#""use client";
export default function SegmentError({ error }: { error: Error }) {
  return <p>{error.message}</p>;
}
"#;
        let analysis = analyze_route_file("app/dashboard/error.tsx", error_boundary);
        assert_eq!(analysis.module_directives, vec!["use client"]);
        let default = analysis.default_export.expect("default export");
        assert_eq!(default.name.as_deref(), Some("SegmentError"));

        let incomplete = "export function NotFound() { return null; }\n";
        let analysis = analyze_route_file("app/not-found.tsx", incomplete);
        assert!(analysis.default_export.is_none());
        assert!(analysis.module_directives.is_empty());
    }

    #[test]
    fn parse_panic_degrades_to_the_labeled_textual_scan() {
        let broken =
            "export async function GET( {{{{\n'use server';\nexport async function saveUser() {}\n";
        let analysis = analyze_route_file("app/api/broken/route.ts", broken);
        assert_eq!(analysis.engine, ENGINE_DEGRADED_TEXTUAL);
        assert!(analysis.parse_error_count > 0);
        assert_eq!(analysis.methods.len(), 1);
        assert_eq!(analysis.methods[0].method, "GET");
        assert_eq!(analysis.methods[0].export_kind, "textual");
        assert_eq!(
            &broken[analysis.methods[0].start_byte..analysis.methods[0].end_byte],
            "export async function GET"
        );
        // The recovery scan is the legacy detector verbatim, which also
        // collected the exported handler name in a `use server` file.
        assert_eq!(
            analysis
                .server_actions
                .iter()
                .filter_map(|action| action.name.as_deref())
                .collect::<Vec<_>>(),
            legacy_exported_function_names(broken)
        );
        assert!(
            analysis
                .server_actions
                .iter()
                .any(|action| action.name.as_deref() == Some("saveUser"))
        );
    }

    /// Every case the previous string-matching detector reported must still be
    /// reported by the real AST analysis.
    #[test]
    fn string_matching_regression_corpus_is_still_detected() {
        let corpus = [
            (
                "app/api/one/route.ts",
                "export async function GET() { return new Response(); }\n",
            ),
            (
                "app/api/two/route.ts",
                "export function POST() { return new Response(); }\n",
            ),
            (
                "app/api/three/route.ts",
                "export const PUT = () => new Response();\nexport const DELETE = handler;\n",
            ),
            (
                "app/api/four/route.ts",
                "export async function GET() {}\nexport async function HEAD() {}\nexport function OPTIONS() {}\n",
            ),
        ];
        for (path, source) in corpus {
            let expected = legacy_route_methods(source);
            let analysis = analyze_route_file(path, source);
            assert_eq!(analysis.engine, ENGINE_OXC_AST);
            let mut found = analysis
                .methods
                .iter()
                .map(|method| method.method.clone())
                .collect::<Vec<_>>();
            found.sort();
            let mut expected_sorted = expected.clone();
            expected_sorted.sort();
            assert_eq!(found, expected_sorted, "lost methods for {path}");
        }

        let action_sources = [
            "'use server';\nexport async function saveUser() {}\n",
            "\"use server\";\nexport function syncUsers() {}\n",
            "'use server';\nexport const removeUser = async () => {};\n",
        ];
        for source in action_sources {
            let expected = legacy_exported_function_names(source);
            let analysis = analyze_route_file("app/actions.ts", source);
            assert_eq!(analysis.engine, ENGINE_OXC_AST);
            for name in &expected {
                assert!(
                    analysis
                        .server_actions
                        .iter()
                        .any(|action| action.name.as_deref() == Some(name)),
                    "lost action {name} in {source:?}"
                );
            }
        }
    }

    /// The precision cases the string matcher got wrong and the AST must not.
    #[test]
    fn ast_analysis_rejects_string_matching_false_positives() {
        let source = "// export async function GET\nconst usage = \"export const POST\";\nexport const GETTER = 1;\n";
        assert!(
            !legacy_route_methods(source).is_empty(),
            "the legacy scan false-positives here"
        );
        let analysis = analyze_route_file("app/api/clean/route.ts", source);
        assert_eq!(analysis.engine, ENGINE_OXC_AST);
        assert!(analysis.methods.is_empty());
    }

    /// The previous `nextjs::route_methods` detection, kept verbatim as the
    /// regression oracle.
    fn legacy_route_methods(source: &str) -> Vec<String> {
        let mut methods = Vec::new();
        for method in ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"] {
            let patterns = [
                format!("export async function {method}"),
                format!("export function {method}"),
                format!("export const {method}"),
            ];
            if patterns.iter().any(|pattern| source.contains(pattern)) {
                methods.push(method.to_string());
            }
        }
        methods
    }

    /// The previous `nextjs::exported_function_names` detection, kept verbatim
    /// as the regression oracle.
    fn legacy_exported_function_names(source: &str) -> Vec<String> {
        let mut names = Vec::new();
        for line in source.lines() {
            let trimmed = line.trim_start();
            for prefix in [
                "export async function ",
                "export function ",
                "export const ",
            ] {
                if let Some(rest) = trimmed.strip_prefix(prefix) {
                    let name = rest
                        .split(|character: char| {
                            !(character.is_alphanumeric() || character == '_' || character == '$')
                        })
                        .next()
                        .unwrap_or_default();
                    if !name.is_empty() && !names.iter().any(|existing| existing == name) {
                        names.push(name.to_string());
                    }
                }
            }
        }
        names
    }

    #[test]
    fn version_gate_resolves_catalog_pins_and_degrades_unknown_versions() {
        let directory = tempfile::tempdir().expect("tempdir");
        let app = directory.path().join("apps/web");
        std::fs::create_dir_all(&app).expect("app dir");
        std::fs::write(
            directory.path().join("pnpm-workspace.yaml"),
            "packages:\n  - apps/*\ncatalog:\n  next: 16.3.0-preview.6\n",
        )
        .expect("workspace");
        std::fs::write(
            app.join("package.json"),
            "{\"name\":\"web\",\"dependencies\":{\"next\":\"catalog:\"}}",
        )
        .expect("manifest");
        let gate = next_version_gate(&app, "apps/web");
        assert_eq!(gate.resolved.as_deref(), Some("16.3.0-preview.6"));
        assert!(gate.pinned);
        assert_eq!(gate.mode, VERSION_GATE_FULL);
        assert!(gate.reason.is_none());

        std::fs::write(
            app.join("package.json"),
            "{\"name\":\"web\",\"dependencies\":{\"next\":\"15.5.0\"}}",
        )
        .expect("manifest");
        let gate = next_version_gate(&app, "apps/web");
        assert_eq!(gate.resolved.as_deref(), Some("15.5.0"));
        assert!(!gate.pinned);
        assert_eq!(gate.mode, VERSION_GATE_SAFE);
        assert!(
            gate.reason
                .as_deref()
                .is_some_and(|reason| reason.contains("not pinned"))
        );

        std::fs::write(app.join("package.json"), "{\"name\":\"web\"}").expect("manifest");
        let gate = next_version_gate(&app, "apps/web");
        assert!(gate.declared.is_none());
        assert_eq!(gate.mode, VERSION_GATE_SAFE);
    }

    #[test]
    fn matrix_next_versions_match_the_embedded_contract() {
        let versions = matrix_next_versions();
        assert!(versions.contains(&"16.3.0-preview.6".to_string()));
    }
}
