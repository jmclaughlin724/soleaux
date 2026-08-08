//! Static Next.js route, boundary, and Server Action intelligence.
//!
//! Route identity comes from the file convention; methods, segment config,
//! actions, and boundary facts come from the real Oxc AST analysis in
//! [`crate::nextjs_oxc`]. The static provider is authoritative when no dev
//! server is running. Runtime DevTools evidence is merged only after
//! capability discovery by the adapter, never by this index.

use crate::nextjs_oxc::{
    self, BOUNDARY_STEMS, CrossAppRoute, ENGINE_DEGRADED_TEXTUAL, ENGINE_OXC_AST, HTTP_METHODS,
    MethodExport, NextVersionGate, RouteFileAnalysis, SegmentConfigExport,
};
use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeSet,
    fs,
    path::{Path, PathBuf},
};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct NextRoute {
    pub route: String,
    pub file: String,
    pub router: String,
    pub kind: String,
    pub methods: Vec<String>,
    pub method_exports: Vec<MethodExport>,
    pub segment_config: Vec<SegmentConfigExport>,
    pub app_root: String,
    pub source: String,
    pub confidence: String,
    /// The analysis engine that produced methods and segment config:
    /// `oxc-ast`, `degraded-textual-scan`, or `file-convention` when the file
    /// was not analyzed.
    pub extraction_engine: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ServerAction {
    pub name: String,
    pub file: String,
    pub app_root: String,
    /// `module` for a `"use server"` file export, `function` for an inline
    /// `"use server"` function body.
    pub scope: String,
    pub exported: bool,
    pub start_byte: usize,
    pub end_byte: usize,
    pub source: String,
    pub confidence: String,
}

/// One app-router boundary file (`layout`, `template`, `loading`, `error`,
/// `global-error`, `not-found`, `default`) with its AST-verified facts.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct NextBoundary {
    pub boundary: String,
    pub route: String,
    pub file: String,
    pub app_root: String,
    pub has_default_export: bool,
    pub default_export_name: Option<String>,
    pub use_client: bool,
    pub default_export_start_byte: Option<usize>,
    pub default_export_end_byte: Option<usize>,
    pub source: String,
    pub confidence: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct NextIndex {
    pub applications: Vec<String>,
    pub routes: Vec<NextRoute>,
    pub server_actions: Vec<ServerAction>,
    pub boundaries: Vec<NextBoundary>,
    /// Cross-application reconciliation of the merged route list.
    pub cross_app_routes: Vec<CrossAppRoute>,
    /// Per-application version gate against the embedded matrix pins.
    pub version_gates: Vec<NextVersionGate>,
    pub provider: String,
    pub runtime_evidence_attached: bool,
}

pub fn index_nextjs(root: &Path) -> Result<NextIndex> {
    let root = fs::canonicalize(root).with_context(|| format!("resolving {}", root.display()))?;
    let applications = discover_applications(&root)?;
    let mut routes = Vec::new();
    let mut server_actions = Vec::new();
    let mut boundaries = Vec::new();
    let mut version_gates = Vec::new();
    for application in &applications {
        version_gates.push(nextjs_oxc::next_version_gate(
            application,
            &relative_path(&root, application),
        ));
        for (directory, router) in [
            (application.join("app"), "app"),
            (application.join("src/app"), "app"),
            (application.join("pages"), "pages"),
            (application.join("src/pages"), "pages"),
        ] {
            if !directory.is_dir() {
                continue;
            }
            walk_files(&directory, 100_000, &mut |path| {
                let Some(extension) = path.extension().and_then(|value| value.to_str()) else {
                    return;
                };
                if !matches!(extension, "js" | "jsx" | "ts" | "tsx" | "mjs" | "mts") {
                    return;
                }
                let Ok(source) = fs::read_to_string(path) else {
                    return;
                };
                let relative = relative_path(&root, path);
                let analysis = nextjs_oxc::analyze_route_file(&relative, &source);
                if let Some(route) =
                    route_from_file(&root, application, &directory, router, path, &analysis)
                {
                    routes.push(route);
                }
                if router == "app"
                    && let Some(boundary) =
                        boundary_from_file(&root, application, &directory, path, &analysis)
                {
                    boundaries.push(boundary);
                }
                let (action_source, action_confidence) = action_provenance(&analysis);
                for action in &analysis.server_actions {
                    server_actions.push(ServerAction {
                        name: action
                            .name
                            .clone()
                            .unwrap_or_else(|| "(anonymous)".to_string()),
                        file: relative.clone(),
                        app_root: relative_path(&root, application),
                        scope: action.scope.clone(),
                        exported: action.exported,
                        start_byte: action.start_byte,
                        end_byte: action.end_byte,
                        source: action_source.to_string(),
                        confidence: action_confidence.to_string(),
                    });
                }
            })?;
        }
    }
    routes.sort_by(|left, right| {
        left.route
            .cmp(&right.route)
            .then_with(|| left.file.cmp(&right.file))
    });
    routes.dedup_by(|left, right| {
        left.route == right.route && left.file == right.file && left.kind == right.kind
    });
    server_actions.sort_by(|left, right| {
        (&left.file, &left.name, left.start_byte).cmp(&(&right.file, &right.name, right.start_byte))
    });
    server_actions.dedup_by(|left, right| {
        left.file == right.file && left.name == right.name && left.start_byte == right.start_byte
    });
    boundaries.sort_by(|left, right| {
        (&left.route, &left.boundary, &left.file).cmp(&(&right.route, &right.boundary, &right.file))
    });
    let cross_app_routes = nextjs_oxc::reconcile_cross_app(&routes);
    Ok(NextIndex {
        applications: applications
            .iter()
            .map(|path| relative_path(&root, path))
            .collect(),
        routes,
        server_actions,
        boundaries,
        cross_app_routes,
        version_gates,
        provider: "soleaux-static-next-provider".to_string(),
        runtime_evidence_attached: false,
    })
}

fn discover_applications(root: &Path) -> Result<Vec<PathBuf>> {
    let mut candidates = BTreeSet::new();
    if is_next_application(root) {
        candidates.insert(root.to_path_buf());
    }
    for container in ["apps", "packages"] {
        let directory = root.join(container);
        if !directory.is_dir() {
            continue;
        }
        for entry in fs::read_dir(directory)? {
            let path = entry?.path();
            if path.is_dir() && is_next_application(&path) {
                candidates.insert(path);
            }
        }
    }
    Ok(candidates.into_iter().collect())
}

fn is_next_application(path: &Path) -> bool {
    [
        "next.config.js",
        "next.config.mjs",
        "next.config.ts",
        "next.config.cjs",
    ]
    .iter()
    .any(|name| path.join(name).is_file())
        || path.join("app").is_dir()
        || path.join("src/app").is_dir()
        || path.join("pages").is_dir()
        || path.join("src/pages").is_dir()
}

fn route_from_file(
    workspace_root: &Path,
    application: &Path,
    router_root: &Path,
    router: &str,
    path: &Path,
    analysis: &RouteFileAnalysis,
) -> Option<NextRoute> {
    let stem = path.file_stem()?.to_str()?;
    let relative = path.strip_prefix(router_root).ok()?;
    let mut segments = relative
        .parent()
        .map(normalized_segments)
        .unwrap_or_default();
    let (kind, methods, method_exports, segment_config) = if router == "app" {
        match stem {
            "page" => (
                "page",
                Vec::new(),
                Vec::new(),
                analysis.segment_config.clone(),
            ),
            "route" => (
                "route_handler",
                canonical_methods(&analysis.methods),
                analysis.methods.clone(),
                analysis.segment_config.clone(),
            ),
            "sitemap" | "robots" | "manifest" => (
                "metadata_route",
                Vec::new(),
                Vec::new(),
                analysis.segment_config.clone(),
            ),
            _ => return None,
        }
    } else {
        if stem.starts_with('_') {
            return None;
        }
        if stem != "index" {
            segments.push(normalize_segment(stem));
        }
        let api = segments.first().is_some_and(|segment| segment == "api");
        (
            if api { "api_route" } else { "page" },
            Vec::new(),
            Vec::new(),
            Vec::new(),
        )
    };
    let route = if segments.is_empty() {
        "/".to_string()
    } else {
        format!("/{}", segments.join("/"))
    };
    Some(NextRoute {
        route,
        file: relative_path(workspace_root, path),
        router: router.to_string(),
        kind: kind.to_string(),
        methods,
        method_exports,
        segment_config,
        app_root: relative_path(workspace_root, application),
        source: "static".to_string(),
        confidence: "verified_file_convention".to_string(),
        extraction_engine: analysis.engine.clone(),
    })
}

fn boundary_from_file(
    workspace_root: &Path,
    application: &Path,
    router_root: &Path,
    path: &Path,
    analysis: &RouteFileAnalysis,
) -> Option<NextBoundary> {
    let stem = path.file_stem()?.to_str()?;
    if !BOUNDARY_STEMS.contains(&stem) {
        return None;
    }
    let relative = path.strip_prefix(router_root).ok()?;
    let segments = relative
        .parent()
        .map(normalized_segments)
        .unwrap_or_default();
    let route = if segments.is_empty() {
        "/".to_string()
    } else {
        format!("/{}", segments.join("/"))
    };
    let confidence = match analysis.engine.as_str() {
        engine if engine == ENGINE_OXC_AST => "verified_ast",
        engine if engine == ENGINE_DEGRADED_TEXTUAL => "degraded_textual_scan",
        _ => "verified_file_convention",
    };
    Some(NextBoundary {
        boundary: stem.to_string(),
        route,
        file: relative_path(workspace_root, path),
        app_root: relative_path(workspace_root, application),
        has_default_export: analysis.default_export.is_some(),
        default_export_name: analysis
            .default_export
            .as_ref()
            .and_then(|export| export.name.clone()),
        use_client: analysis
            .module_directives
            .iter()
            .any(|directive| directive == "use client"),
        default_export_start_byte: analysis
            .default_export
            .as_ref()
            .map(|export| export.start_byte),
        default_export_end_byte: analysis
            .default_export
            .as_ref()
            .map(|export| export.end_byte),
        source: "static".to_string(),
        confidence: confidence.to_string(),
    })
}

/// Method names in the canonical HTTP order the previous detector emitted.
fn canonical_methods(exports: &[MethodExport]) -> Vec<String> {
    HTTP_METHODS
        .iter()
        .filter(|method| exports.iter().any(|export| export.method == **method))
        .map(|method| (*method).to_string())
        .collect()
}

fn action_provenance(analysis: &RouteFileAnalysis) -> (&'static str, &'static str) {
    if analysis.engine == ENGINE_OXC_AST {
        ("static-oxc-ast", "verified_ast")
    } else {
        ("static-degraded-textual-scan", "degraded_textual_scan")
    }
}

fn normalized_segments(path: &Path) -> Vec<String> {
    path.components()
        .filter_map(|component| component.as_os_str().to_str())
        .filter_map(|segment| {
            if segment.starts_with('(') && segment.ends_with(')') && !segment.starts_with("(.)") {
                return None;
            }
            if segment.starts_with('@') {
                return None;
            }
            Some(normalize_segment(segment))
        })
        .filter(|segment| !segment.is_empty())
        .collect()
}

fn normalize_segment(segment: &str) -> String {
    let mut value = segment.to_string();
    for prefix in ["(..)(..)", "(...)", "(..)", "(.)"] {
        if value.starts_with(prefix) {
            value = value[prefix.len()..].to_string();
            break;
        }
    }
    if value.starts_with("[[...") && value.ends_with("]]") {
        return format!("*{}?", &value[5..value.len() - 2]);
    }
    if value.starts_with("[...") && value.ends_with(']') {
        return format!("*{}", &value[4..value.len() - 1]);
    }
    if value.starts_with('[') && value.ends_with(']') {
        return format!(":{}", &value[1..value.len() - 1]);
    }
    value
}

fn walk_files(root: &Path, maximum: usize, visitor: &mut impl FnMut(&Path)) -> Result<()> {
    let mut stack = vec![root.to_path_buf()];
    let mut count = 0usize;
    while let Some(directory) = stack.pop() {
        for entry in fs::read_dir(directory)? {
            let path = entry?.path();
            if path.is_dir() {
                stack.push(path);
            } else if path.is_file() {
                visitor(&path);
                count += 1;
                if count >= maximum {
                    return Ok(());
                }
            }
        }
    }
    Ok(())
}

fn relative_path(root: &Path, path: &Path) -> String {
    path.strip_prefix(root)
        .unwrap_or(path)
        .to_string_lossy()
        .replace('\\', "/")
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn indexes_app_pages_dynamic_routes_handlers_and_server_actions() {
        let directory = tempdir().expect("tempdir");
        fs::create_dir_all(directory.path().join("app/users/[id]")).expect("route dirs");
        fs::write(
            directory.path().join("next.config.mjs"),
            "export default {};",
        )
        .expect("config");
        fs::write(
            directory.path().join("app/users/[id]/page.tsx"),
            "export default function Page() { return null; }",
        )
        .expect("page");
        fs::write(
            directory.path().join("app/users/[id]/route.ts"),
            "export async function GET() { return new Response(); }",
        )
        .expect("handler");
        fs::write(
            directory.path().join("app/actions.ts"),
            "'use server';\nexport async function saveUser() {}\n",
        )
        .expect("action");
        let index = index_nextjs(directory.path()).expect("index");
        assert!(
            index
                .routes
                .iter()
                .any(|route| route.route == "/users/:id" && route.kind == "page")
        );
        assert!(
            index
                .routes
                .iter()
                .any(|route| route.methods == vec!["GET"])
        );
        assert!(
            index
                .server_actions
                .iter()
                .any(|action| action.name == "saveUser")
        );
        assert!(!index.runtime_evidence_attached);
    }

    #[test]
    fn route_handler_methods_come_from_the_ast_with_spans() {
        let directory = tempdir().expect("tempdir");
        fs::create_dir_all(directory.path().join("app/api/items")).expect("route dirs");
        fs::write(
            directory.path().join("next.config.mjs"),
            "export default {};",
        )
        .expect("config");
        let handler = "export const revalidate = 30;\nexport async function GET() { return new Response(); }\nexport const POST = async () => new Response();\n// export function DELETE is only a comment\n";
        fs::write(directory.path().join("app/api/items/route.ts"), handler).expect("handler");
        let index = index_nextjs(directory.path()).expect("index");
        let route = index
            .routes
            .iter()
            .find(|route| route.kind == "route_handler")
            .expect("route handler");
        assert_eq!(route.methods, vec!["GET", "POST"]);
        assert_eq!(route.extraction_engine, "oxc-ast");
        assert_eq!(route.method_exports.len(), 2);
        let get = &route.method_exports[0];
        assert_eq!(get.method, "GET");
        assert!(handler[get.start_byte..get.end_byte].starts_with("async function GET"));
        assert_eq!(
            route
                .segment_config
                .iter()
                .map(|config| (config.name.as_str(), config.value.as_deref()))
                .collect::<Vec<_>>(),
            vec![("revalidate", Some("30"))]
        );
    }

    #[test]
    fn boundaries_are_indexed_with_ast_facts() {
        let directory = tempdir().expect("tempdir");
        fs::create_dir_all(directory.path().join("app/dashboard")).expect("dirs");
        fs::write(
            directory.path().join("next.config.mjs"),
            "export default {};",
        )
        .expect("config");
        fs::write(
            directory.path().join("app/layout.tsx"),
            "export default function RootLayout({ children }) { return children; }",
        )
        .expect("layout");
        fs::write(
            directory.path().join("app/dashboard/loading.tsx"),
            "export default function Loading() { return null; }",
        )
        .expect("loading");
        fs::write(
            directory.path().join("app/dashboard/error.tsx"),
            "'use client';\nexport default function DashboardError() { return null; }\n",
        )
        .expect("error");
        fs::write(
            directory.path().join("app/dashboard/page.tsx"),
            "export default function Page() { return null; }",
        )
        .expect("page");
        let index = index_nextjs(directory.path()).expect("index");
        assert_eq!(
            index
                .boundaries
                .iter()
                .map(|boundary| (boundary.route.as_str(), boundary.boundary.as_str()))
                .collect::<Vec<_>>(),
            vec![
                ("/", "layout"),
                ("/dashboard", "error"),
                ("/dashboard", "loading"),
            ]
        );
        let error = index
            .boundaries
            .iter()
            .find(|boundary| boundary.boundary == "error")
            .expect("error boundary");
        assert!(error.use_client);
        assert!(error.has_default_export);
        assert_eq!(error.default_export_name.as_deref(), Some("DashboardError"));
        assert_eq!(error.confidence, "verified_ast");
        assert!(
            !index
                .routes
                .iter()
                .any(|route| route.file.ends_with("layout.tsx")),
            "boundaries never enter the route list"
        );
    }

    #[test]
    fn multi_app_merge_reconciles_conflicting_and_disjoint_routes() {
        let directory = tempdir().expect("tempdir");
        for (app, pages) in [
            ("apps/web", vec!["", "pricing", "blog"]),
            ("apps/admin", vec!["", "pricing", "audit"]),
        ] {
            for page in pages {
                let segment = if page.is_empty() {
                    directory.path().join(app).join("app")
                } else {
                    directory.path().join(app).join("app").join(page)
                };
                fs::create_dir_all(&segment).expect("dirs");
                fs::write(
                    segment.join("page.tsx"),
                    "export default function Page() { return null; }",
                )
                .expect("page");
            }
            fs::write(
                directory.path().join(app).join("next.config.mjs"),
                "export default {};",
            )
            .expect("config");
            fs::write(
                directory.path().join(app).join("package.json"),
                "{\"name\":\"fixture\",\"dependencies\":{\"next\":\"16.3.0-preview.6\"}}",
            )
            .expect("manifest");
        }
        let index = index_nextjs(directory.path()).expect("index");
        assert_eq!(index.applications, vec!["apps/admin", "apps/web"]);
        let conflicts = index
            .cross_app_routes
            .iter()
            .filter(|entry| entry.status == "conflict")
            .map(|entry| entry.route.as_str())
            .collect::<Vec<_>>();
        assert_eq!(conflicts, vec!["/", "/pricing"]);
        let pricing = index
            .cross_app_routes
            .iter()
            .find(|entry| entry.route == "/pricing")
            .expect("pricing");
        assert_eq!(pricing.apps, vec!["apps/admin", "apps/web"]);
        assert_eq!(pricing.files.len(), 2);
        let unique = index
            .cross_app_routes
            .iter()
            .filter(|entry| entry.status == "unique")
            .map(|entry| entry.route.as_str())
            .collect::<Vec<_>>();
        assert_eq!(unique, vec!["/audit", "/blog"]);
        assert_eq!(index.version_gates.len(), 2);
        assert!(index.version_gates.iter().all(|gate| gate.mode == "full"));
    }

    #[test]
    fn version_gate_degrades_apps_without_a_pinned_next_version() {
        let directory = tempdir().expect("tempdir");
        fs::create_dir_all(directory.path().join("app")).expect("dirs");
        fs::write(
            directory.path().join("next.config.mjs"),
            "export default {};",
        )
        .expect("config");
        fs::write(
            directory.path().join("app/page.tsx"),
            "export default function Page() { return null; }",
        )
        .expect("page");
        let index = index_nextjs(directory.path()).expect("index");
        assert_eq!(index.version_gates.len(), 1);
        assert_eq!(index.version_gates[0].mode, "safe");
        assert!(index.version_gates[0].reason.is_some());
    }
}
