//! Supervised JSONL worker for structural search and rewrite.
//!
//! Protocol: one JSON request per stdin line, one JSON response per stdout
//! line, logs on stderr only. Matching and rewriting reuse the ast-grep
//! 0.44.1 crates end to end: `SerializableRuleConfig` deserialization,
//! `RuleCore` matching (including metavariable transforms), and the `Fixer`
//! template plus expansion machinery.

use std::borrow::Cow;
use std::io::BufRead;
use std::str::FromStr;

use ast_grep_config::{Fixer, GlobalRules, RuleCore, SerializableRuleConfig};
use ast_grep_core::matcher::Pattern;
use ast_grep_core::meta_var::{MetaVarEnv, MetaVariable};
use ast_grep_core::tree_sitter::{LanguageExt, StrDoc};
use ast_grep_core::{Doc, Matcher, Node, NodeMatch};
use ast_grep_language::SupportLang;
use base64::Engine as _;
use base64::engine::general_purpose::STANDARD as BASE64;
use bit_set::BitSet;
use serde_json::{Map, Value, json};

pub const ENGINE: &str = "rust";
/// Mirrors the exact `=0.44.1` pin in `Cargo.toml`.
pub const ENGINE_VERSION: &str = "0.44.1";
/// The exact Soleaux structural protocol implemented by this worker.
pub const CAPABILITIES: [&str; 1] = ["soleaux.structural/v1"];
/// One JSONL frame may not exceed 8 MiB, matching the Python worker.
pub const MAX_FRAME_BYTES: usize = 8 * 1024 * 1024;

const DEFAULT_MAX_FINDINGS: usize = 1000;
const DEFAULT_MAX_CAPTURE_CHARS: usize = 200;
const DEFAULT_MAX_PREVIEW_CHARS: usize = 200;
const MAX_CAPTURES_PER_FINDING: usize = 16;

type WorkerDoc = StrDoc<SupportLang>;

/// One typed per-request failure, serialized as `{"error": {"type", "message"}}`.
pub struct WorkerError {
    kind: &'static str,
    message: String,
}

impl WorkerError {
    fn new(kind: &'static str, message: impl Into<String>) -> Self {
        Self {
            kind,
            message: message.into(),
        }
    }
}

fn error_chain(error: &dyn std::error::Error) -> String {
    let mut message = error.to_string();
    let mut source = error.source();
    while let Some(cause) = source {
        message.push_str(": ");
        message.push_str(&cause.to_string());
        source = cause.source();
    }
    message
}

/// One stdin frame: a bounded payload line or an oversized rejection marker.
pub enum Frame {
    Line(Vec<u8>),
    Oversized,
}

/// Read one newline-delimited frame, enforcing the 8 MiB cap.
///
/// Oversized lines are fully drained from the reader and reported as
/// [`Frame::Oversized`] so the worker can reject the frame and keep serving.
pub fn read_frame(reader: &mut impl BufRead) -> std::io::Result<Option<Frame>> {
    let mut line: Vec<u8> = Vec::new();
    let mut oversized = false;
    loop {
        let (consumed, saw_newline) = {
            let buffered = reader.fill_buf()?;
            if buffered.is_empty() {
                if oversized {
                    return Ok(Some(Frame::Oversized));
                }
                if line.is_empty() {
                    return Ok(None);
                }
                return Ok(Some(Frame::Line(line)));
            }
            match buffered.iter().position(|&byte| byte == b'\n') {
                Some(position) => {
                    if !oversized {
                        line.extend_from_slice(&buffered[..position]);
                    }
                    (position + 1, true)
                }
                None => {
                    if !oversized {
                        line.extend_from_slice(buffered);
                    }
                    (buffered.len(), false)
                }
            }
        };
        reader.consume(consumed);
        if line.len() > MAX_FRAME_BYTES {
            oversized = true;
            line = Vec::new();
        }
        if saw_newline {
            if oversized {
                return Ok(Some(Frame::Oversized));
            }
            return Ok(Some(Frame::Line(line)));
        }
    }
}

/// The response for one frame, plus whether the worker must exit afterwards.
pub enum Outcome {
    Reply(Value),
    Shutdown(Value),
}

fn error_reply(id: Value, error: WorkerError) -> Value {
    json!({
        "id": id,
        "error": {"type": error.kind, "message": error.message},
    })
}

/// The reply for a frame rejected by the [`MAX_FRAME_BYTES`] cap.
pub fn oversized_frame_reply() -> Value {
    error_reply(
        Value::Null,
        WorkerError::new("protocol", "frame exceeds the 8 MiB cap"),
    )
}

/// Handle one JSONL request frame and produce exactly one response value.
pub fn handle_frame(frame: &[u8]) -> Outcome {
    let request: Value = match serde_json::from_slice(frame) {
        Ok(value) => value,
        Err(error) => {
            return Outcome::Reply(error_reply(
                Value::Null,
                WorkerError::new(
                    "protocol",
                    format!("request line is not valid JSON: {error}"),
                ),
            ));
        }
    };
    let id = request.get("id").cloned().unwrap_or(Value::Null);
    let Some(op) = request.get("op").and_then(Value::as_str) else {
        return Outcome::Reply(error_reply(
            id,
            WorkerError::new("protocol", "request requires a string \"op\""),
        ));
    };
    match op {
        "ping" => Outcome::Reply(json!({
            "id": id,
            "ok": true,
            "engine": ENGINE,
            "engine_version": ENGINE_VERSION,
            "capabilities": CAPABILITIES,
        })),
        "shutdown" => Outcome::Shutdown(json!({"id": id, "ok": true})),
        "structural" => Outcome::Reply(match structural(&request) {
            Ok(mut response) => {
                response["id"] = id;
                response
            }
            Err(error) => error_reply(id, error),
        }),
        unknown => Outcome::Reply(error_reply(
            id,
            WorkerError::new("protocol", format!("unknown op {unknown:?}")),
        )),
    }
}

struct Limits {
    max_findings: usize,
    max_capture_chars: usize,
    max_preview_chars: usize,
}

impl Limits {
    fn parse(value: Option<&Value>) -> Result<Self, WorkerError> {
        let mut limits = Self {
            max_findings: DEFAULT_MAX_FINDINGS,
            max_capture_chars: DEFAULT_MAX_CAPTURE_CHARS,
            max_preview_chars: DEFAULT_MAX_PREVIEW_CHARS,
        };
        let Some(value) = value.filter(|value| !value.is_null()) else {
            return Ok(limits);
        };
        let object = value
            .as_object()
            .ok_or_else(|| WorkerError::new("protocol", "limits must be an object"))?;
        for (key, slot) in [
            ("max_findings", &mut limits.max_findings),
            ("max_capture_chars", &mut limits.max_capture_chars),
            ("max_preview_chars", &mut limits.max_preview_chars),
        ] {
            if let Some(raw) = object.get(key).filter(|value| !value.is_null()) {
                let parsed = raw.as_u64().ok_or_else(|| {
                    WorkerError::new(
                        "protocol",
                        format!("limits.{key} must be a non-negative integer"),
                    )
                })?;
                *slot = usize::try_from(parsed).map_err(|_| {
                    WorkerError::new("protocol", format!("limits.{key} is out of range"))
                })?;
            }
        }
        Ok(limits)
    }
}

enum WorkerMatcher {
    Pattern(Pattern),
    Rule(Box<RuleCore>),
}

impl Matcher for WorkerMatcher {
    fn match_node_with_env<'tree, D: Doc>(
        &self,
        node: Node<'tree, D>,
        env: &mut Cow<MetaVarEnv<'tree, D>>,
    ) -> Option<Node<'tree, D>> {
        match self {
            Self::Pattern(pattern) => pattern.match_node_with_env(node, env),
            Self::Rule(rule) => rule.match_node_with_env(node, env),
        }
    }

    fn potential_kinds(&self) -> Option<BitSet> {
        match self {
            Self::Pattern(pattern) => pattern.potential_kinds(),
            Self::Rule(rule) => rule.potential_kinds(),
        }
    }

    fn get_match_len<D: Doc>(&self, node: Node<'_, D>) -> Option<usize> {
        match self {
            Self::Pattern(pattern) => pattern.get_match_len(node),
            Self::Rule(rule) => rule.get_match_len(node),
        }
    }
}

struct Compiled {
    matcher: WorkerMatcher,
    fixer: Option<Fixer>,
}

/// Convert one contract transform (`{"kind": "replace", ...}`) into the
/// upstream externally tagged `Transformation` object form. Only the three
/// stable transformations are mapped; everything else is rejected.
fn convert_transforms(value: &Value) -> Result<Value, WorkerError> {
    let object = value
        .as_object()
        .ok_or_else(|| WorkerError::new("protocol", "transforms must be an object"))?;
    let mut converted = Map::new();
    for (variable, spec) in object {
        let spec = spec.as_object().ok_or_else(|| {
            WorkerError::new(
                "protocol",
                format!("transform for {variable:?} must be an object"),
            )
        })?;
        let kind = spec.get("kind").and_then(Value::as_str).ok_or_else(|| {
            WorkerError::new(
                "protocol",
                format!("transform for {variable:?} requires a string \"kind\""),
            )
        })?;
        let mapped_keys: &[(&str, &str)] = match kind {
            "replace" => &[("source", "source"), ("replace", "replace"), ("by", "by")],
            "substring" => &[
                ("source", "source"),
                ("start_char", "startChar"),
                ("end_char", "endChar"),
            ],
            "convert" => &[
                ("source", "source"),
                ("to_case", "toCase"),
                ("separated_by", "separatedBy"),
            ],
            unsupported => {
                return Err(WorkerError::new(
                    "unsupported_capability",
                    format!(
                        "transform kind {unsupported:?} is outside the stable \
                         replace/substring/convert set"
                    ),
                ));
            }
        };
        let mut inner = Map::new();
        for (wire_key, upstream_key) in mapped_keys {
            if let Some(raw) = spec.get(*wire_key).filter(|value| !value.is_null()) {
                inner.insert((*upstream_key).to_string(), raw.clone());
            }
        }
        converted.insert(variable.clone(), json!({kind: Value::Object(inner)}));
    }
    Ok(Value::Object(converted))
}

/// Normalize the wire fix into upstream `SerializableFixer` JSON: a plain
/// template string, or a fix object with `expandStart`/`expandEnd` relations
/// built from the supplied ast-grep patterns.
fn convert_fix(value: &Value) -> Result<Value, WorkerError> {
    let object = value
        .as_object()
        .ok_or_else(|| WorkerError::new("protocol", "fix must be an object"))?;
    if let Some(text) = object.get("text").filter(|value| !value.is_null()) {
        let text = text
            .as_str()
            .ok_or_else(|| WorkerError::new("protocol", "fix.text must be a string"))?;
        return Ok(Value::String(text.to_string()));
    }
    let template = object
        .get("template")
        .and_then(Value::as_str)
        .ok_or_else(|| {
            WorkerError::new("protocol", "fix requires \"text\" or a string \"template\"")
        })?;
    let expansion = |key: &str| -> Result<Option<Value>, WorkerError> {
        match object.get(key).filter(|value| !value.is_null()) {
            None => Ok(None),
            Some(raw) => {
                let pattern = raw.as_str().ok_or_else(|| {
                    WorkerError::new(
                        "protocol",
                        format!("fix.{key} must be an ast-grep pattern string"),
                    )
                })?;
                Ok(Some(json!({"pattern": pattern})))
            }
        }
    };
    let expand_start = expansion("expand_start")?;
    let expand_end = expansion("expand_end")?;
    if expand_start.is_none() && expand_end.is_none() {
        return Ok(Value::String(template.to_string()));
    }
    let mut fix = Map::new();
    fix.insert("template".to_string(), Value::String(template.to_string()));
    if let Some(relation) = expand_start {
        fix.insert("expandStart".to_string(), relation);
    }
    if let Some(relation) = expand_end {
        fix.insert("expandEnd".to_string(), relation);
    }
    Ok(Value::Object(fix))
}

/// Build the matcher and fixer through ast-grep-config's own deserialization
/// (`SerializableRuleConfig` -> `RuleCore` + `Fixer::parse`), so rule,
/// constraints, utils, transform, and fix semantics stay upstream-defined.
fn compile_via_config(
    lang: SupportLang,
    language_name: &str,
    rule: Value,
    constraints: Option<Value>,
    utils: Option<Value>,
    transform: Option<Value>,
    fix: Option<Value>,
) -> Result<Compiled, WorkerError> {
    let mut config = Map::new();
    config.insert(
        "id".to_string(),
        Value::String("soleaux-inline".to_string()),
    );
    config.insert(
        "language".to_string(),
        Value::String(language_name.to_string()),
    );
    config.insert("rule".to_string(), rule);
    if let Some(value) = constraints {
        config.insert("constraints".to_string(), value);
    }
    if let Some(value) = utils {
        config.insert("utils".to_string(), value);
    }
    if let Some(value) = transform {
        config.insert("transform".to_string(), value);
    }
    if let Some(value) = fix {
        config.insert("fix".to_string(), value);
    }
    let serializable: SerializableRuleConfig<SupportLang> =
        serde_json::from_value(Value::Object(config)).map_err(|error| {
            WorkerError::new(
                "invalid_matcher",
                format!("matcher rejected by ast-grep-config: {error}"),
            )
        })?;
    let matcher = serializable
        .get_matcher(&GlobalRules::default())
        .map_err(|error| WorkerError::new("invalid_matcher", error_chain(&error)))?;
    let fixer = match &serializable.fix {
        None => None,
        Some(fix) => {
            let env = matcher.get_env(lang);
            let mut fixers = Fixer::parse(fix, &env, &serializable.core.transform)
                .map_err(|error| WorkerError::new("invalid_matcher", error_chain(&error)))?;
            if fixers.is_empty() {
                None
            } else {
                Some(fixers.remove(0))
            }
        }
    };
    Ok(Compiled {
        matcher: WorkerMatcher::Rule(Box::new(matcher)),
        fixer,
    })
}

fn compile(
    lang: SupportLang,
    language_name: &str,
    matcher: &Map<String, Value>,
    fix: Option<&Value>,
    transforms: Option<&Value>,
) -> Result<Compiled, WorkerError> {
    let kind = matcher
        .get("kind")
        .and_then(Value::as_str)
        .ok_or_else(|| WorkerError::new("protocol", "matcher requires a string \"kind\""))?;
    let transform = transforms.map(convert_transforms).transpose()?;
    let fix = fix.map(convert_fix).transpose()?;
    match kind {
        "pattern" => {
            let pattern_source =
                matcher
                    .get("pattern")
                    .and_then(Value::as_str)
                    .ok_or_else(|| {
                        WorkerError::new(
                            "protocol",
                            "pattern matcher requires a string \"pattern\"",
                        )
                    })?;
            let plain_fix = matches!(&fix, None | Some(Value::String(_)));
            if transform.is_none() && plain_fix {
                let pattern = Pattern::try_new(pattern_source, lang)
                    .map_err(|error| WorkerError::new("invalid_matcher", error_chain(&error)))?;
                let fixer = match &fix {
                    Some(Value::String(template)) => {
                        Some(Fixer::from_str(template, &lang).map_err(|error| {
                            WorkerError::new("invalid_matcher", error_chain(&error))
                        })?)
                    }
                    _ => None,
                };
                return Ok(Compiled {
                    matcher: WorkerMatcher::Pattern(pattern),
                    fixer,
                });
            }
            // Transforms and fix expansion live in ast-grep-config; wrap the
            // pattern in its equivalent `rule: {pattern}` form to reuse them.
            compile_via_config(
                lang,
                language_name,
                json!({"pattern": pattern_source}),
                None,
                None,
                transform,
                fix,
            )
        }
        "rule" => {
            let rule = matcher
                .get("rule")
                .filter(|value| value.is_object())
                .cloned()
                .ok_or_else(|| {
                    WorkerError::new("protocol", "rule matcher requires an object \"rule\"")
                })?;
            let optional_object = |key: &str| -> Result<Option<Value>, WorkerError> {
                match matcher.get(key).filter(|value| !value.is_null()) {
                    None => Ok(None),
                    Some(value) if value.is_object() => Ok(Some(value.clone())),
                    Some(_) => Err(WorkerError::new(
                        "protocol",
                        format!("matcher {key} must be an object"),
                    )),
                }
            };
            let constraints = optional_object("constraints")?;
            let utils = optional_object("utils")?;
            compile_via_config(
                lang,
                language_name,
                rule,
                constraints,
                utils,
                transform,
                fix,
            )
        }
        unknown => Err(WorkerError::new(
            "invalid_matcher",
            format!("unsupported matcher kind {unknown:?}"),
        )),
    }
}

fn truncate_chars(text: &str, max_chars: usize) -> String {
    text.chars().take(max_chars).collect()
}

fn collect_captures(
    node_match: &NodeMatch<'_, WorkerDoc>,
    source: &str,
    max_capture_chars: usize,
) -> Vec<Value> {
    let env = node_match.get_env();
    let mut captures: Vec<(String, String, usize, usize)> = Vec::new();
    for variable in env.get_matched_variables() {
        match variable {
            MetaVariable::Capture(name, _) => {
                // Transformed variables surface here too but carry no node;
                // only real single-node captures have byte coordinates.
                if let Some(node) = env.get_match(&name) {
                    let range = node.range();
                    captures.push((
                        name,
                        truncate_chars(&node.text(), max_capture_chars),
                        range.start,
                        range.end,
                    ));
                }
            }
            MetaVariable::MultiCapture(name) => {
                let nodes = env.get_multiple_matches(&name);
                if let (Some(first), Some(last)) = (nodes.first(), nodes.last()) {
                    let start = first.range().start;
                    let end = last.range().end;
                    let text = source.get(start..end).unwrap_or_default();
                    captures.push((name, truncate_chars(text, max_capture_chars), start, end));
                }
            }
            MetaVariable::Dropped(_) | MetaVariable::Multiple => {}
        }
    }
    captures.sort();
    captures.truncate(MAX_CAPTURES_PER_FINDING);
    captures
        .into_iter()
        .map(|(name, text, byte_start, byte_end)| {
            json!({
                "name": name,
                "text": text,
                "byte_start": byte_start,
                "byte_end": byte_end,
            })
        })
        .collect()
}

fn finding_json(
    path: &str,
    source: &str,
    node_match: &NodeMatch<'_, WorkerDoc>,
    limits: &Limits,
) -> Value {
    let node = node_match.get_node();
    let range = node.range();
    let start = node.start_pos();
    let end = node.end_pos();
    json!({
        "path": path,
        "byte_start": range.start,
        "byte_end": range.end,
        "start_line": start.line(),
        "start_column": start.column(node),
        "end_line": end.line(),
        "end_column": end.column(node),
        "text_preview": truncate_chars(&node.text(), limits.max_preview_chars),
        "captures": collect_captures(node_match, source, limits.max_capture_chars),
    })
}

/// Keep only outermost matches: any match strictly inside (or sharing the
/// exact range of) a kept match is dropped; a partial overlap between
/// surviving match ranges is a request-level `overlapping_edits` error.
fn outermost_indices(
    matches: &[NodeMatch<'_, WorkerDoc>],
    path: &str,
) -> Result<Vec<usize>, WorkerError> {
    let mut order: Vec<usize> = (0..matches.len()).collect();
    order.sort_by(|&left, &right| {
        let left_range = matches[left].get_node().range();
        let right_range = matches[right].get_node().range();
        left_range
            .start
            .cmp(&right_range.start)
            .then(right_range.end.cmp(&left_range.end))
            .then(left.cmp(&right))
    });
    let mut kept: Vec<usize> = Vec::new();
    for index in order {
        let range = matches[index].get_node().range();
        if let Some(&last) = kept.last() {
            let last_range = matches[last].get_node().range();
            if range.start < last_range.end {
                if range.end <= last_range.end {
                    continue;
                }
                return Err(WorkerError::new(
                    "overlapping_edits",
                    format!(
                        "match ranges {}..{} and {}..{} in {path} partially overlap",
                        last_range.start, last_range.end, range.start, range.end
                    ),
                ));
            }
        }
        kept.push(index);
    }
    Ok(kept)
}

fn structural(request: &Value) -> Result<Value, WorkerError> {
    let language_name = request
        .get("language")
        .and_then(Value::as_str)
        .ok_or_else(|| WorkerError::new("protocol", "structural requires a string \"language\""))?;
    let lang = SupportLang::from_str(language_name).map_err(|_| {
        WorkerError::new(
            "unsupported_language",
            format!("language {language_name:?} is not a registered ast-grep language"),
        )
    })?;
    let limits = Limits::parse(request.get("limits"))?;
    let want_edits = match request.get("want").filter(|value| !value.is_null()) {
        None => false,
        Some(value) => {
            let entries = value
                .as_array()
                .ok_or_else(|| WorkerError::new("protocol", "want must be an array of strings"))?;
            let mut edits = false;
            for entry in entries {
                match entry.as_str() {
                    Some("findings") => {}
                    Some("edits") => edits = true,
                    _ => {
                        return Err(WorkerError::new(
                            "protocol",
                            "want entries must be \"findings\" or \"edits\"",
                        ));
                    }
                }
            }
            edits
        }
    };
    let matcher_value = request
        .get("matcher")
        .and_then(Value::as_object)
        .ok_or_else(|| WorkerError::new("protocol", "structural requires an object \"matcher\""))?;
    let fix = request.get("fix").filter(|value| !value.is_null());
    let transforms = request.get("transforms").filter(|value| !value.is_null());
    let files = request
        .get("files")
        .and_then(Value::as_array)
        .ok_or_else(|| WorkerError::new("protocol", "structural requires an array \"files\""))?;
    let compiled = compile(lang, language_name, matcher_value, fix, transforms)?;

    let mut findings: Vec<Value> = Vec::new();
    let mut edits: Vec<Value> = Vec::new();
    let mut errors: Vec<Value> = Vec::new();
    let mut truncated = false;
    for file in files {
        let path = file.get("path").and_then(Value::as_str).ok_or_else(|| {
            WorkerError::new("protocol", "file entries require a string \"path\"")
        })?;
        let content_b64 = file
            .get("content_b64")
            .and_then(Value::as_str)
            .ok_or_else(|| {
                WorkerError::new("protocol", "file entries require a string \"content_b64\"")
            })?;
        let bytes = match BASE64.decode(content_b64) {
            Ok(bytes) => bytes,
            Err(error) => {
                errors.push(json!({
                    "path": path,
                    "message": format!("content_b64 is not valid base64: {error}"),
                }));
                continue;
            }
        };
        let source = match String::from_utf8(bytes) {
            Ok(source) => source,
            Err(_) => {
                errors.push(json!({
                    "path": path,
                    "message": "content is not valid UTF-8",
                }));
                continue;
            }
        };
        let root = lang.ast_grep(source.as_str());
        let root_node = root.root();
        let matches: Vec<NodeMatch<'_, WorkerDoc>> =
            root_node.find_all(&compiled.matcher).collect();
        for node_match in &matches {
            if findings.len() == limits.max_findings {
                truncated = true;
                break;
            }
            findings.push(finding_json(path, &source, node_match, &limits));
        }
        if want_edits && let Some(fixer) = &compiled.fixer {
            let mut file_edits: Vec<(usize, usize, String)> = Vec::new();
            for index in outermost_indices(&matches, path)? {
                let edit = matches[index].make_edit(&compiled.matcher, fixer);
                file_edits.push((
                    edit.position,
                    edit.position + edit.deleted_length,
                    String::from_utf8_lossy(&edit.inserted_text).into_owned(),
                ));
            }
            file_edits.sort_by_key(|(start, end, _)| (*start, *end));
            for window in file_edits.windows(2) {
                if window[1].0 < window[0].1 {
                    return Err(WorkerError::new(
                        "overlapping_edits",
                        format!(
                            "expanded edit ranges {}..{} and {}..{} in {path} overlap",
                            window[0].0, window[0].1, window[1].0, window[1].1
                        ),
                    ));
                }
            }
            for (byte_start, byte_end, inserted_text) in file_edits {
                edits.push(json!({
                    "path": path,
                    "byte_start": byte_start,
                    "byte_end": byte_end,
                    "inserted_text": inserted_text,
                }));
            }
        }
    }
    Ok(json!({
        "engine": ENGINE,
        "engine_version": ENGINE_VERSION,
        "findings": findings,
        "edits": edits,
        "truncated": truncated,
        "errors": errors,
    }))
}
