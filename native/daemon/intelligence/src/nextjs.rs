//! Static Next.js route and Server Action intelligence.
//!
//! The static provider is authoritative when no dev server is running. Runtime
//! DevTools evidence is merged only after capability discovery by the adapter.

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
    pub app_root: String,
    pub source: String,
    pub confidence: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ServerAction {
    pub name: String,
    pub file: String,
    pub app_root: String,
    pub source: String,
    pub confidence: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct NextIndex {
    pub applications: Vec<String>,
    pub routes: Vec<NextRoute>,
    pub server_actions: Vec<ServerAction>,
    pub provider: String,
    pub runtime_evidence_attached: bool,
}

pub fn index_nextjs(root: &Path) -> Result<NextIndex> {
    let root = fs::canonicalize(root).with_context(|| format!("resolving {}", root.display()))?;
    let applications = discover_applications(&root)?;
    let mut routes = Vec::new();
    let mut server_actions = Vec::new();
    for application in &applications {
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
                if let Some(route) = route_from_file(&root, application, &directory, router, path) {
                    routes.push(route);
                }
                if let Ok(source) = fs::read_to_string(path)
                    && (source.contains("use server")
                        || source.contains("'use server'")
                        || source.contains("\"use server\""))
                {
                    for name in exported_function_names(&source) {
                        server_actions.push(ServerAction {
                            name,
                            file: relative_path(&root, path),
                            app_root: relative_path(&root, application),
                            source: "static-oxc-compatible-scan".to_string(),
                            confidence: "structural".to_string(),
                        });
                    }
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
        left.file
            .cmp(&right.file)
            .then_with(|| left.name.cmp(&right.name))
    });
    server_actions.dedup_by(|left, right| left.file == right.file && left.name == right.name);
    Ok(NextIndex {
        applications: applications
            .iter()
            .map(|path| relative_path(&root, path))
            .collect(),
        routes,
        server_actions,
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
) -> Option<NextRoute> {
    let extension = path.extension()?.to_str()?;
    if !matches!(extension, "js" | "jsx" | "ts" | "tsx" | "mjs" | "mts") {
        return None;
    }
    let stem = path.file_stem()?.to_str()?;
    let relative = path.strip_prefix(router_root).ok()?;
    let mut segments = relative
        .parent()
        .map(normalized_segments)
        .unwrap_or_default();
    let (kind, methods) = if router == "app" {
        match stem {
            "page" => ("page", Vec::new()),
            "route" => ("route_handler", route_methods(path)),
            "sitemap" | "robots" | "manifest" => ("metadata_route", Vec::new()),
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
        (if api { "api_route" } else { "page" }, Vec::new())
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
        app_root: relative_path(workspace_root, application),
        source: "static".to_string(),
        confidence: "verified_file_convention".to_string(),
    })
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

fn route_methods(path: &Path) -> Vec<String> {
    let Ok(source) = fs::read_to_string(path) else {
        return Vec::new();
    };
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

fn exported_function_names(source: &str) -> Vec<String> {
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
    }
}
