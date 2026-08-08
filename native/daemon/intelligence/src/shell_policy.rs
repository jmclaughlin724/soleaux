//! Shell execution-policy intelligence over the tree-sitter-bash CST.
//!
//! Implements the fourteen-item shell list from the transcript gap audit §6:
//! permissive semantic parsing, optional ShellCheck, executable provenance,
//! argument provenance, pipeline/redirection/substitution semantics, effect
//! classification, approval preview, sandbox specification, process-tree
//! capture specification, resource/output limits, redaction, changed-file
//! reconciliation, diagnostics, and audit receipts.
//!
//! `mvdan.cc/sh` is Go-only (BSD-3) with no machine-readable AST export and no
//! Rust binding, so in-process integration is impossible; the approved
//! permissive semantic parser is tree-sitter-bash (MIT) plus this hand-written
//! semantics and effect layer. ShellCheck (GPL-3.0) and `shfmt` (BSD-3) are
//! invoked only as optional external probes when present on `PATH` and are
//! never linked or bundled, so GPL code stays out of core.
//!
//! The daemon does not execute shell today. This module owns the analysis and
//! the policy decision; [`SandboxSpec`], [`ResourceLimits`], and
//! [`ProcessCaptureSpec`] are the binding contract a future executor must
//! enforce, and [`ShellAuditReceipt`] reconciles that contract against what
//! the executor observed. Every unknown — a dynamic executable, an
//! unclassified command, an unparseable script — fails closed into an
//! approval requirement or an outright refusal, never into silence.

use anyhow::{Context, Result, bail};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tree_sitter::{Node, Parser as TreeSitterParser};
use uuid::Uuid;

use crate::Diagnostic;

pub const SHELL_SEMANTICS_ENGINE: &str = "tree-sitter-bash+soleaux-shell-policy";
pub const SHELL_SEMANTICS_VERSION: &str = "1";
pub const SHELL_APPROVAL_SCHEMA_VERSION: &str = "soleaux.shell-approval/v1";
pub const SHELL_GRANT_SCHEMA_VERSION: &str = "soleaux.shell-grant/v1";
pub const SHELL_AUDIT_SCHEMA_VERSION: &str = "soleaux.shell-audit/v1";

/// Commands analyzed per script before truncation is reported.
pub const MAX_ANALYZED_COMMANDS: usize = 512;
const NESTED_SHELL_DEPTH_LIMIT: usize = 4;
const PROBE_OUTPUT_CAP_BYTES: usize = 256 * 1024;

// ---------------------------------------------------------------------------
// Effect classification vocabulary
// ---------------------------------------------------------------------------

/// Effect class of one command or one whole script. Ordering is by severity;
/// the two unknown classes dominate every known class so that classification
/// gaps fail closed.
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum EffectClass {
    ReadOnly,
    FilesystemWrite,
    PackageManagement,
    NetworkAccess,
    ProcessControl,
    Destructive,
    Privileged,
    /// The executable or its payload is computed at runtime.
    DynamicUnknown,
    /// The executable is static but not in the classification tables.
    Unknown,
}

impl EffectClass {
    fn severity(self) -> u8 {
        match self {
            Self::ReadOnly => 0,
            Self::FilesystemWrite => 1,
            Self::PackageManagement | Self::NetworkAccess | Self::ProcessControl => 2,
            Self::Destructive | Self::Privileged => 3,
            Self::DynamicUnknown | Self::Unknown => 4,
        }
    }

    /// The `soleaux-vault` `RiskLevel` serde name a grant must reach to run
    /// this effect class. Unknowns demand the highest level: fail closed.
    pub fn required_risk_level(self) -> &'static str {
        match self {
            Self::ReadOnly => "read_only",
            Self::FilesystemWrite => "local_write",
            Self::ProcessControl => "process",
            Self::PackageManagement | Self::NetworkAccess => "network",
            Self::Destructive | Self::Privileged | Self::DynamicUnknown | Self::Unknown => {
                "privileged"
            }
        }
    }
}

fn max_effect(left: EffectClass, right: EffectClass) -> EffectClass {
    if right.severity() > left.severity() {
        right
    } else {
        left
    }
}

// ---------------------------------------------------------------------------
// Semantic analysis types
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ExecutableKind {
    Builtin,
    /// A function defined in this script.
    ScriptFunction,
    /// A bare word resolved through `PATH` at runtime.
    Literal,
    AbsolutePath,
    RelativePath,
    /// The executable comes from an expansion, substitution, or dynamic
    /// concatenation and cannot be statically determined.
    Dynamic,
    /// The command has no name node (for example an assignment-only command).
    None,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ExecutableProvenance {
    pub text: String,
    pub kind: ExecutableKind,
    /// Statically resolved program name (basename for paths); `None` when
    /// dynamic or absent.
    pub resolved: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ArgumentProvenance {
    pub text: String,
    /// One of `literal`, `single_quoted`, `ansi_c_quoted`, `double_quoted`,
    /// `variable_expansion`, `command_substitution`, `process_substitution`,
    /// `arithmetic_expansion`, `glob`, `brace_expansion`, `concatenation`,
    /// `pattern`, `other`.
    pub origin: String,
    /// The runtime value cannot be statically determined.
    pub dynamic: bool,
    /// Static unquoted value when the argument is fully static.
    pub static_value: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct RedirectionAnalysis {
    pub operator: String,
    /// One of `input`, `output`, `append`, `read_write`, `duplicate_fd`,
    /// `output_both`, `append_both`, `heredoc`, `herestring`, `unknown`.
    pub kind: String,
    pub descriptor: Option<u32>,
    pub target: Option<ArgumentProvenance>,
    pub writes_file: bool,
    pub start_byte: usize,
    pub end_byte: usize,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum CommandContext {
    TopLevel,
    Function,
    CommandSubstitution,
    ProcessSubstitution,
    Subshell,
    Heredoc,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct CommandAnalysis {
    pub source: String,
    pub start_byte: usize,
    pub end_byte: usize,
    pub start_row: usize,
    pub end_row: usize,
    pub context: CommandContext,
    /// Enclosing function name when `context` is `function`.
    pub enclosing_function: Option<String>,
    pub executable: ExecutableProvenance,
    pub arguments: Vec<ArgumentProvenance>,
    /// `VAR=value` prefixes on this command.
    pub environment_assignments: Vec<String>,
    pub redirections: Vec<RedirectionAnalysis>,
    pub pipeline_index: Option<usize>,
    pub negated: bool,
    pub effect: EffectClass,
    pub effect_reasons: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct PipelineAnalysis {
    pub start_byte: usize,
    pub end_byte: usize,
    pub stage_count: usize,
    /// `|&` merges stderr into the pipe.
    pub stderr_merged: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct SubstitutionAnalysis {
    /// One of `command`, `backquote`, `process_input`, `process_output`,
    /// `arithmetic`.
    pub kind: String,
    pub start_byte: usize,
    pub end_byte: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct AssignmentAnalysis {
    pub name: String,
    pub value_text: String,
    pub static_value: bool,
    pub start_byte: usize,
    pub end_byte: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ShellScriptAnalysis {
    pub engine: String,
    pub engine_version: String,
    pub semantics_version: String,
    pub parse_valid: bool,
    pub commands: Vec<CommandAnalysis>,
    pub pipelines: Vec<PipelineAnalysis>,
    pub substitutions: Vec<SubstitutionAnalysis>,
    pub assignments: Vec<AssignmentAnalysis>,
    pub functions: Vec<String>,
    pub has_background_jobs: bool,
    pub overall_effect: EffectClass,
    pub diagnostics: Vec<Diagnostic>,
    pub truncated: bool,
}

// ---------------------------------------------------------------------------
// Tree walk
// ---------------------------------------------------------------------------

fn node_text(node: Node<'_>, source: &[u8]) -> String {
    node.utf8_text(source).unwrap_or_default().to_owned()
}

fn named_children<'tree>(node: Node<'tree>) -> Vec<Node<'tree>> {
    (0..u32::try_from(node.named_child_count()).unwrap_or(u32::MAX))
        .filter_map(|index| node.named_child(index))
        .collect()
}

fn anonymous_kinds(node: Node<'_>) -> Vec<String> {
    (0..u32::try_from(node.child_count()).unwrap_or(u32::MAX))
        .filter_map(|index| node.child(index))
        .filter(|child| !child.is_named())
        .map(|child| child.kind().to_owned())
        .collect()
}

fn is_glob_word(text: &str) -> bool {
    let mut escaped = false;
    for character in text.chars() {
        if escaped {
            escaped = false;
            continue;
        }
        match character {
            '\\' => escaped = true,
            '*' | '?' | '[' => return true,
            _ => {}
        }
    }
    false
}

/// Static unquoted value of a node, when every part is static.
fn static_text(node: Node<'_>, source: &[u8]) -> Option<String> {
    match node.kind() {
        "word" | "number" => {
            let text = node_text(node, source);
            (!is_glob_word(&text)).then_some(text)
        }
        "raw_string" => {
            let text = node_text(node, source);
            Some(
                text.trim_start_matches('\'')
                    .trim_end_matches('\'')
                    .to_owned(),
            )
        }
        "ansi_c_string" => {
            let text = node_text(node, source);
            Some(
                text.trim_start_matches("$'")
                    .trim_end_matches('\'')
                    .to_owned(),
            )
        }
        "string" => {
            let children = named_children(node);
            children
                .iter()
                .all(|child| child.kind() == "string_content")
                .then(|| {
                    children
                        .iter()
                        .map(|child| node_text(*child, source))
                        .collect::<String>()
                })
        }
        "concatenation" => {
            let mut value = String::new();
            for child in named_children(node) {
                value.push_str(&static_text(child, source)?);
            }
            Some(value)
        }
        _ => None,
    }
}

fn argument_provenance(node: Node<'_>, source: &[u8]) -> ArgumentProvenance {
    let text = node_text(node, source);
    let static_value = static_text(node, source);
    let (origin, dynamic) = match node.kind() {
        "word" if is_glob_word(&text) => ("glob", true),
        "word" | "number" => ("literal", false),
        "raw_string" => ("single_quoted", false),
        "ansi_c_string" => ("ansi_c_quoted", false),
        "string" => ("double_quoted", static_value.is_none()),
        "simple_expansion" | "expansion" => ("variable_expansion", true),
        "command_substitution" => ("command_substitution", true),
        "process_substitution" => ("process_substitution", true),
        "arithmetic_expansion" => ("arithmetic_expansion", true),
        "brace_expression" => ("brace_expansion", true),
        "concatenation" => ("concatenation", static_value.is_none()),
        "extglob_pattern" | "regex" => ("pattern", true),
        _ => ("other", true),
    };
    ArgumentProvenance {
        text,
        origin: origin.to_owned(),
        dynamic,
        static_value,
    }
}

const SHELL_BUILTINS: &[&str] = &[
    ":", ".", "[", "alias", "bg", "cd", "command", "declare", "dirs", "disown", "echo", "eval",
    "exec", "exit", "export", "false", "fg", "getopts", "hash", "help", "jobs", "let", "local",
    "popd", "printf", "pushd", "pwd", "read", "readonly", "return", "set", "shift", "shopt",
    "source", "test", "times", "trap", "true", "type", "typeset", "ulimit", "umask", "unalias",
    "unset", "wait",
];

fn executable_provenance(
    name_node: Option<Node<'_>>,
    source: &[u8],
    functions: &BTreeSet<String>,
) -> ExecutableProvenance {
    let Some(name_node) = name_node else {
        return ExecutableProvenance {
            text: String::new(),
            kind: ExecutableKind::None,
            resolved: None,
        };
    };
    let inner = name_node.named_child(0).unwrap_or(name_node);
    let text = node_text(name_node, source);
    let Some(value) = static_text(inner, source) else {
        return ExecutableProvenance {
            text,
            kind: ExecutableKind::Dynamic,
            resolved: None,
        };
    };
    let kind = if value.starts_with('/') {
        ExecutableKind::AbsolutePath
    } else if value.contains('/') {
        ExecutableKind::RelativePath
    } else if functions.contains(&value) {
        ExecutableKind::ScriptFunction
    } else if SHELL_BUILTINS.contains(&value.as_str()) {
        ExecutableKind::Builtin
    } else {
        ExecutableKind::Literal
    };
    let resolved = match kind {
        ExecutableKind::AbsolutePath | ExecutableKind::RelativePath => Path::new(&value)
            .file_name()
            .map(|name| name.to_string_lossy().to_string()),
        _ => Some(value.clone()),
    };
    ExecutableProvenance {
        text,
        kind,
        resolved,
    }
}

fn redirection_kind(operator: &str, target: Option<&ArgumentProvenance>) -> (String, bool) {
    let duplicates_fd = target.is_some_and(|target| {
        target
            .static_value
            .as_deref()
            .is_some_and(|value| value.parse::<u32>().is_ok() || value == "-")
    });
    match operator {
        "<" => ("input".to_owned(), false),
        ">" | ">|" => ("output".to_owned(), true),
        ">>" => ("append".to_owned(), true),
        "<>" => ("read_write".to_owned(), true),
        "&>" => ("output_both".to_owned(), true),
        "&>>" => ("append_both".to_owned(), true),
        ">&" if duplicates_fd => ("duplicate_fd".to_owned(), false),
        ">&" => ("output".to_owned(), true),
        "<&" => ("duplicate_fd".to_owned(), false),
        "<<" | "<<-" => ("heredoc".to_owned(), false),
        "<<<" => ("herestring".to_owned(), false),
        // Fail closed: an operator this layer does not recognize is treated
        // as a file write.
        _ => ("unknown".to_owned(), true),
    }
}

const REDIRECT_OPERATORS: &[&str] = &[
    "<", ">", ">>", "<&", ">&", "&>", "&>>", "<>", ">|", "<<", "<<-", "<<<",
];

fn build_redirection(node: Node<'_>, source: &[u8]) -> RedirectionAnalysis {
    let descriptor = node
        .child_by_field_name("descriptor")
        .and_then(|fd| node_text(fd, source).parse::<u32>().ok());
    let operator = anonymous_kinds(node)
        .into_iter()
        .find(|kind| REDIRECT_OPERATORS.contains(&kind.as_str()))
        .unwrap_or_else(|| match node.kind() {
            "herestring_redirect" => "<<<".to_owned(),
            "heredoc_redirect" => "<<".to_owned(),
            _ => String::new(),
        });
    let target = match node.kind() {
        "file_redirect" => node
            .child_by_field_name("destination")
            .map(|destination| argument_provenance(destination, source)),
        "herestring_redirect" => named_children(node)
            .into_iter()
            .find(|child| child.kind() != "file_descriptor")
            .map(|child| argument_provenance(child, source)),
        "heredoc_redirect" => named_children(node)
            .into_iter()
            .find(|child| child.kind() == "heredoc_start")
            .map(|child| argument_provenance(child, source)),
        _ => None,
    };
    let (kind, writes_file) = redirection_kind(&operator, target.as_ref());
    RedirectionAnalysis {
        operator,
        kind,
        descriptor,
        target,
        writes_file,
        start_byte: node.start_byte(),
        end_byte: node.end_byte(),
    }
}

fn collect_function_names(node: Node<'_>, source: &[u8], names: &mut BTreeSet<String>) {
    if node.kind() == "function_definition"
        && let Some(name) = node.child_by_field_name("name")
    {
        names.insert(node_text(name, source));
    }
    for child in named_children(node) {
        collect_function_names(child, source, names);
    }
}

struct Walker<'source> {
    source: &'source [u8],
    functions: BTreeSet<String>,
    commands: Vec<CommandAnalysis>,
    pipelines: Vec<PipelineAnalysis>,
    substitutions: Vec<SubstitutionAnalysis>,
    assignments: Vec<AssignmentAnalysis>,
    has_background_jobs: bool,
    dropped_commands: usize,
    depth: usize,
}

#[derive(Clone, Copy)]
struct WalkState<'tree> {
    context: CommandContext,
    enclosing_function: Option<Node<'tree>>,
    pipeline_index: Option<usize>,
    negated: bool,
}

impl<'source> Walker<'source> {
    fn visit(&mut self, node: Node<'_>, state: WalkState<'_>) {
        let mut child_state = WalkState {
            pipeline_index: None,
            negated: false,
            ..state
        };
        match node.kind() {
            "function_definition" => {
                child_state.context = CommandContext::Function;
                for child in named_children(node) {
                    let function_state = WalkState {
                        enclosing_function: node.child_by_field_name("name"),
                        ..child_state
                    };
                    self.visit(child, function_state);
                }
                return;
            }
            "pipeline" => {
                let stage_count = named_children(node)
                    .iter()
                    .filter(|child| child.kind() != "comment")
                    .count();
                self.pipelines.push(PipelineAnalysis {
                    start_byte: node.start_byte(),
                    end_byte: node.end_byte(),
                    stage_count,
                    stderr_merged: anonymous_kinds(node).iter().any(|kind| kind == "|&"),
                });
                child_state.pipeline_index = Some(self.pipelines.len() - 1);
            }
            "negated_command" => {
                child_state.negated = true;
            }
            "redirected_statement" => {
                let redirects = named_children(node)
                    .into_iter()
                    .filter(|child| {
                        matches!(
                            child.kind(),
                            "file_redirect" | "heredoc_redirect" | "herestring_redirect"
                        )
                    })
                    .map(|child| build_redirection(child, self.source))
                    .collect::<Vec<_>>();
                if let Some(body) = node.child_by_field_name("body") {
                    self.visit_command_like(body, state, &redirects);
                }
                for child in named_children(node) {
                    if Some(child.id()) == node.child_by_field_name("body").map(|body| body.id()) {
                        continue;
                    }
                    match child.kind() {
                        "file_redirect" | "herestring_redirect" => {
                            for target in named_children(child) {
                                self.visit(target, child_state);
                            }
                        }
                        _ => self.visit(child, child_state),
                    }
                }
                return;
            }
            "command" => {
                self.visit_command_like(node, state, &[]);
                return;
            }
            "command_substitution" => {
                let backquote = self
                    .source
                    .get(node.start_byte())
                    .is_some_and(|byte| *byte == b'`');
                self.substitutions.push(SubstitutionAnalysis {
                    kind: if backquote { "backquote" } else { "command" }.to_owned(),
                    start_byte: node.start_byte(),
                    end_byte: node.end_byte(),
                });
                child_state.context = CommandContext::CommandSubstitution;
            }
            "process_substitution" => {
                let output = self
                    .source
                    .get(node.start_byte())
                    .is_some_and(|byte| *byte == b'>');
                self.substitutions.push(SubstitutionAnalysis {
                    kind: if output {
                        "process_output"
                    } else {
                        "process_input"
                    }
                    .to_owned(),
                    start_byte: node.start_byte(),
                    end_byte: node.end_byte(),
                });
                child_state.context = CommandContext::ProcessSubstitution;
            }
            "arithmetic_expansion" => {
                self.substitutions.push(SubstitutionAnalysis {
                    kind: "arithmetic".to_owned(),
                    start_byte: node.start_byte(),
                    end_byte: node.end_byte(),
                });
            }
            "subshell" => {
                child_state.context = CommandContext::Subshell;
            }
            "heredoc_body" => {
                child_state.context = CommandContext::Heredoc;
            }
            "variable_assignment" => {
                self.record_assignment(node);
            }
            "list" | "program" if anonymous_kinds(node).iter().any(|kind| kind == "&") => {
                self.has_background_jobs = true;
            }
            _ => {}
        }
        for child in named_children(node) {
            self.visit(child, child_state);
        }
    }

    /// Visit a redirected statement's body: a direct `command` body absorbs
    /// the statement's redirections; any other body shape keeps its own walk
    /// and the redirections stay recorded at statement level.
    fn visit_command_like(
        &mut self,
        node: Node<'_>,
        state: WalkState<'_>,
        attached: &[RedirectionAnalysis],
    ) {
        if node.kind() != "command" {
            if !attached.iter().any(|redirect| redirect.writes_file) {
                self.visit(node, state);
                return;
            }
            self.visit(node, state);
            // A write redirection on a compound statement is preserved as a
            // synthetic command entry so effect escalation cannot be lost.
            self.push_command(CommandAnalysis {
                source: node_text(node, self.source),
                start_byte: node.start_byte(),
                end_byte: node.end_byte(),
                start_row: node.start_position().row,
                end_row: node.end_position().row,
                context: state.context,
                enclosing_function: state
                    .enclosing_function
                    .map(|name| node_text(name, self.source)),
                executable: ExecutableProvenance {
                    text: String::new(),
                    kind: ExecutableKind::None,
                    resolved: None,
                },
                arguments: Vec::new(),
                environment_assignments: Vec::new(),
                redirections: attached.to_vec(),
                pipeline_index: state.pipeline_index,
                negated: state.negated,
                effect: EffectClass::FilesystemWrite,
                effect_reasons: vec!["write redirection on a compound statement".to_owned()],
            });
            return;
        }
        let mut redirections = attached.to_vec();
        let mut cursor = node.walk();
        redirections.extend(
            node.children_by_field_name("redirect", &mut cursor)
                .map(|redirect| build_redirection(redirect, self.source)),
        );
        let mut arg_cursor = node.walk();
        let arguments = node
            .children_by_field_name("argument", &mut arg_cursor)
            .map(|argument| argument_provenance(argument, self.source))
            .collect::<Vec<_>>();
        let environment_assignments = named_children(node)
            .into_iter()
            .filter(|child| child.kind() == "variable_assignment")
            .map(|child| {
                self.record_assignment(child);
                child
                    .child_by_field_name("name")
                    .map(|name| node_text(name, self.source))
                    .unwrap_or_default()
            })
            .collect::<Vec<_>>();
        let executable = executable_provenance(
            node.child_by_field_name("name"),
            self.source,
            &self.functions,
        );
        let (effect, effect_reasons) =
            classify_command(&executable, &arguments, &redirections, self.depth);
        self.push_command(CommandAnalysis {
            source: node_text(node, self.source),
            start_byte: node.start_byte(),
            end_byte: node.end_byte(),
            start_row: node.start_position().row,
            end_row: node.end_position().row,
            context: state.context,
            enclosing_function: state
                .enclosing_function
                .map(|name| node_text(name, self.source)),
            executable,
            arguments,
            environment_assignments,
            redirections,
            pipeline_index: state.pipeline_index,
            negated: state.negated,
            effect,
            effect_reasons,
        });
        // Recurse for substitutions nested inside arguments, redirect
        // targets, and assignment values.
        let substate = WalkState {
            pipeline_index: None,
            negated: false,
            ..state
        };
        for child in named_children(node) {
            if child.kind() == "command_name" {
                for inner in named_children(child) {
                    self.visit(inner, substate);
                }
            } else {
                self.visit(child, substate);
            }
        }
    }

    fn push_command(&mut self, command: CommandAnalysis) {
        if self.commands.len() >= MAX_ANALYZED_COMMANDS {
            self.dropped_commands += 1;
            return;
        }
        self.commands.push(command);
    }

    fn record_assignment(&mut self, node: Node<'_>) {
        let Some(name) = node.child_by_field_name("name") else {
            return;
        };
        let value = node.child_by_field_name("value");
        self.assignments.push(AssignmentAnalysis {
            name: node_text(name, self.source),
            value_text: value
                .map(|value| node_text(value, self.source))
                .unwrap_or_default(),
            static_value: value.is_none_or(|value| static_text(value, self.source).is_some()),
            start_byte: node.start_byte(),
            end_byte: node.end_byte(),
        });
    }
}

// ---------------------------------------------------------------------------
// Effect classification
// ---------------------------------------------------------------------------

const READ_ONLY_COMMANDS: &[&str] = &[
    "b2sum",
    "basename",
    "bc",
    "cat",
    "cksum",
    "cmp",
    "column",
    "comm",
    "cut",
    "date",
    "df",
    "diff",
    "dirname",
    "du",
    "expand",
    "expr",
    "file",
    "fold",
    "grep",
    "egrep",
    "fgrep",
    "head",
    "hexdump",
    "hostname",
    "id",
    "groups",
    "join",
    "jq",
    "less",
    "ls",
    "lsof",
    "man",
    "md5sum",
    "more",
    "nl",
    "od",
    "paste",
    "pgrep",
    "printenv",
    "ps",
    "readlink",
    "realpath",
    "rg",
    "seq",
    "sha1sum",
    "sha256sum",
    "sha512sum",
    "shasum",
    "sleep",
    "sort",
    "stat",
    "strings",
    "tail",
    "tr",
    "tty",
    "uname",
    "uniq",
    "uptime",
    "wc",
    "which",
    "whereis",
    "whoami",
    "xxd",
    "yes",
];

const FILESYSTEM_WRITE_COMMANDS: &[&str] = &[
    "bzip2", "chgrp", "chmod", "chown", "cp", "csplit", "gunzip", "gzip", "install", "ln", "mkdir",
    "mkfifo", "mknod", "mktemp", "mv", "patch", "rmdir", "split", "sponge", "tar", "tee", "touch",
    "truncate", "unzip", "xz", "zip", "zstd",
];

const DESTRUCTIVE_COMMANDS: &[&str] = &[
    "dd", "diskutil", "fdisk", "mkfs", "parted", "rm", "shred", "wipefs",
];

const NETWORK_COMMANDS: &[&str] = &[
    "aws",
    "az",
    "curl",
    "dig",
    "ftp",
    "gcloud",
    "gh",
    "host",
    "kubectl",
    "nc",
    "ncat",
    "netcat",
    "nslookup",
    "ping",
    "rsync",
    "scp",
    "sftp",
    "socat",
    "ssh",
    "telnet",
    "traceroute",
    "wget",
];

const PROCESS_CONTROL_COMMANDS: &[&str] = &[
    "at",
    "batch",
    "crontab",
    "kill",
    "killall",
    "launchctl",
    "pkill",
    "renice",
    "service",
    "systemctl",
];

const PRIVILEGED_COMMANDS: &[&str] = &[
    "chroot", "halt", "mount", "poweroff", "reboot", "shutdown", "umount",
];

const PACKAGE_MANAGERS: &[&str] = &[
    "apk",
    "apt",
    "apt-get",
    "aptitude",
    "brew",
    "conda",
    "dnf",
    "dpkg",
    "easy_install",
    "flatpak",
    "gem",
    "mamba",
    "pacman",
    "pip",
    "pip3",
    "pipx",
    "port",
    "rpm",
    "snap",
    "yum",
    "zypper",
];

/// Wrappers whose real effect is the wrapped command's effect.
const TRANSPARENT_WRAPPERS: &[&str] =
    &["command", "env", "nice", "nohup", "stdbuf", "time", "xargs"];

const SHELL_STATE_BUILTINS: &[&str] = &[
    ":", "alias", "cd", "declare", "dirs", "disown", "echo", "export", "false", "getopts", "hash",
    "help", "jobs", "let", "local", "popd", "printf", "pushd", "pwd", "read", "readonly", "return",
    "set", "shift", "shopt", "test", "times", "true", "type", "typeset", "ulimit", "umask",
    "unalias", "unset", "wait", "exit", "[",
];

fn static_arguments(arguments: &[ArgumentProvenance]) -> Vec<Option<String>> {
    arguments
        .iter()
        .map(|argument| argument.static_value.clone())
        .collect()
}

fn git_subcommand_effect(subcommand: &str) -> (EffectClass, String) {
    let effect = match subcommand {
        "blame" | "branch" | "cat-file" | "config" | "describe" | "diff" | "grep" | "log"
        | "ls-files" | "ls-tree" | "remote" | "rev-parse" | "shortlog" | "show" | "status"
        | "tag" | "var" | "version" | "worktree" => EffectClass::ReadOnly,
        "add" | "am" | "apply" | "checkout" | "cherry-pick" | "commit" | "gc" | "init"
        | "merge" | "mv" | "prune" | "rebase" | "reset" | "restore" | "revert" | "rm" | "stash"
        | "switch" => EffectClass::FilesystemWrite,
        "clean" => EffectClass::Destructive,
        "clone" | "fetch" | "ls-remote" | "pull" | "push" | "submodule" => {
            EffectClass::NetworkAccess
        }
        _ => EffectClass::Unknown,
    };
    (effect, format!("git subcommand `{subcommand}`"))
}

fn cargo_subcommand_effect(subcommand: &str) -> (EffectClass, String) {
    let effect = match subcommand {
        "metadata" | "tree" | "verify-project" | "version" => EffectClass::ReadOnly,
        "bench" | "build" | "check" | "clean" | "clippy" | "doc" | "fmt" | "test" => {
            EffectClass::FilesystemWrite
        }
        "add" | "install" | "remove" | "update" => EffectClass::PackageManagement,
        "publish" | "search" | "yank" => EffectClass::NetworkAccess,
        "run" => EffectClass::Unknown,
        _ => EffectClass::Unknown,
    };
    (effect, format!("cargo subcommand `{subcommand}`"))
}

fn node_package_manager_effect(subcommand: &str) -> (EffectClass, String) {
    let effect = match subcommand {
        "list" | "ls" | "why" => EffectClass::ReadOnly,
        "add" | "ci" | "i" | "install" | "link" | "prune" | "rebuild" | "remove" | "uninstall"
        | "up" | "update" | "upgrade" => EffectClass::PackageManagement,
        "audit" | "info" | "outdated" | "publish" | "search" | "view" => EffectClass::NetworkAccess,
        _ => EffectClass::Unknown,
    };
    (effect, format!("package-manager subcommand `{subcommand}`"))
}

fn first_positional(static_args: &[Option<String>]) -> Option<(usize, String)> {
    static_args.iter().enumerate().find_map(|(index, value)| {
        let value = value.clone()?;
        (!value.starts_with('-')).then_some((index, value))
    })
}

/// Classify `sh -c` / `bash -c` payloads by recursively analyzing the payload
/// as a script; a dynamic payload fails closed.
fn shell_c_effect(
    static_args: &[Option<String>],
    arguments: &[ArgumentProvenance],
    depth: usize,
) -> (EffectClass, Vec<String>) {
    let Some(flag_index) = arguments
        .iter()
        .position(|argument| argument.static_value.as_deref() == Some("-c"))
    else {
        return (
            EffectClass::Unknown,
            vec!["shell invocation of a script file is not analyzed".to_owned()],
        );
    };
    match static_args.get(flag_index + 1) {
        Some(Some(payload)) => {
            if depth >= NESTED_SHELL_DEPTH_LIMIT {
                return (
                    EffectClass::Unknown,
                    vec!["nested shell payloads exceed the analysis depth limit".to_owned()],
                );
            }
            match analyze_shell_script_at_depth(payload, depth + 1) {
                Ok(inner) if inner.parse_valid => (
                    inner.overall_effect,
                    vec![format!(
                        "shell -c payload classified as {:?}",
                        inner.overall_effect
                    )],
                ),
                _ => (
                    EffectClass::Unknown,
                    vec!["shell -c payload could not be parsed".to_owned()],
                ),
            }
        }
        _ => (
            EffectClass::DynamicUnknown,
            vec!["shell -c payload is dynamic".to_owned()],
        ),
    }
}

fn classify_resolved(
    name: &str,
    arguments: &[ArgumentProvenance],
    depth: usize,
) -> (EffectClass, Vec<String>) {
    let static_args = static_arguments(arguments);
    match name {
        "sudo" | "doas" | "su" => {
            let mut reasons = vec![format!("`{name}` escalates privileges")];
            let inner = first_positional(&static_args);
            let effect = match inner {
                Some((index, inner_name)) => {
                    let (inner_effect, inner_reasons) =
                        classify_resolved(&inner_name, &arguments[index + 1..], depth);
                    reasons.extend(inner_reasons);
                    // The inner effect wins severity ties so `sudo rm` reports
                    // the more specific `destructive`, not the generic wrapper.
                    max_effect(inner_effect, EffectClass::Privileged)
                }
                None if arguments.iter().any(|argument| argument.dynamic) => {
                    reasons.push("wrapped command is dynamic".to_owned());
                    EffectClass::DynamicUnknown
                }
                None => EffectClass::Privileged,
            };
            (effect, reasons)
        }
        "eval" => (
            EffectClass::DynamicUnknown,
            vec!["`eval` executes constructed text".to_owned()],
        ),
        "exec" => match first_positional(&static_args) {
            Some((index, inner_name)) => {
                let (effect, mut reasons) =
                    classify_resolved(&inner_name, &arguments[index + 1..], depth);
                reasons.insert(0, "`exec` replaces the shell process".to_owned());
                (effect, reasons)
            }
            None if arguments.is_empty() => (
                EffectClass::ReadOnly,
                vec!["`exec` with only redirections".to_owned()],
            ),
            None => (
                EffectClass::DynamicUnknown,
                vec!["`exec` target is dynamic".to_owned()],
            ),
        },
        "source" | "." => (
            EffectClass::DynamicUnknown,
            vec!["sourcing executes another file's contents".to_owned()],
        ),
        "trap" => match static_args.first() {
            Some(Some(action)) if action.is_empty() || action == "-" => {
                (EffectClass::ReadOnly, vec!["trap reset".to_owned()])
            }
            _ => (
                EffectClass::DynamicUnknown,
                vec!["`trap` schedules constructed text".to_owned()],
            ),
        },
        "sh" | "bash" | "zsh" | "dash" | "ksh" => shell_c_effect(&static_args, arguments, depth),
        "find" => {
            if static_args
                .iter()
                .any(|argument| argument.as_deref() == Some("-delete"))
            {
                return (
                    EffectClass::Destructive,
                    vec!["`find -delete` removes files".to_owned()],
                );
            }
            if let Some(exec_index) = static_args.iter().position(|argument| {
                matches!(
                    argument.as_deref(),
                    Some("-exec") | Some("-execdir") | Some("-ok") | Some("-okdir")
                )
            }) {
                return match static_args.get(exec_index + 1) {
                    Some(Some(inner_name)) => {
                        let (effect, mut reasons) =
                            classify_resolved(inner_name, &arguments[exec_index + 2..], depth);
                        reasons.insert(0, "`find -exec` runs a command per match".to_owned());
                        (effect, reasons)
                    }
                    _ => (
                        EffectClass::DynamicUnknown,
                        vec!["`find -exec` command is dynamic".to_owned()],
                    ),
                };
            }
            (EffectClass::ReadOnly, vec!["`find` traversal".to_owned()])
        }
        "sed" | "gsed" => {
            let in_place = static_args.iter().any(|argument| {
                argument.as_deref().is_some_and(|value| {
                    value == "-i"
                        || value.starts_with("-i") && value.len() > 2
                        || value.starts_with("--in-place")
                })
            });
            if in_place {
                (
                    EffectClass::FilesystemWrite,
                    vec!["`sed -i` edits files in place".to_owned()],
                )
            } else {
                (EffectClass::ReadOnly, vec!["stream edit".to_owned()])
            }
        }
        "sort" => {
            if static_args.iter().any(|argument| {
                argument
                    .as_deref()
                    .is_some_and(|value| value == "-o" || value.starts_with("--output"))
            }) {
                (
                    EffectClass::FilesystemWrite,
                    vec!["`sort -o` writes its output file".to_owned()],
                )
            } else {
                (EffectClass::ReadOnly, vec!["sort".to_owned()])
            }
        }
        "git" => match first_positional(&static_args) {
            Some((_, subcommand)) => {
                let (effect, reason) = git_subcommand_effect(&subcommand);
                (effect, vec![reason])
            }
            None if arguments.is_empty() => (EffectClass::ReadOnly, vec!["bare git".to_owned()]),
            None => (
                EffectClass::DynamicUnknown,
                vec!["git subcommand is dynamic".to_owned()],
            ),
        },
        "cargo" => match first_positional(&static_args) {
            Some((_, subcommand)) => {
                let (effect, reason) = cargo_subcommand_effect(&subcommand);
                (effect, vec![reason])
            }
            None => (
                EffectClass::Unknown,
                vec!["cargo subcommand is not static".to_owned()],
            ),
        },
        "npm" | "pnpm" | "yarn" | "bun" | "uv" => match first_positional(&static_args) {
            Some((_, subcommand)) => {
                let (effect, reason) = node_package_manager_effect(&subcommand);
                (effect, vec![reason])
            }
            None => (
                EffectClass::Unknown,
                vec!["package-manager subcommand is not static".to_owned()],
            ),
        },
        "timeout" => match first_positional(&static_args) {
            Some((duration_index, _)) => match static_args.get(duration_index + 1..) {
                Some(rest) => match rest.iter().enumerate().find_map(|(offset, value)| {
                    let value = value.clone()?;
                    (!value.starts_with('-')).then_some((duration_index + 1 + offset, value))
                }) {
                    Some((index, inner_name)) => {
                        classify_resolved(&inner_name, &arguments[index + 1..], depth)
                    }
                    None => (
                        EffectClass::DynamicUnknown,
                        vec!["`timeout` command is dynamic".to_owned()],
                    ),
                },
                None => (
                    EffectClass::DynamicUnknown,
                    vec!["`timeout` command is missing".to_owned()],
                ),
            },
            None => (
                EffectClass::DynamicUnknown,
                vec!["`timeout` duration is dynamic".to_owned()],
            ),
        },
        name if TRANSPARENT_WRAPPERS.contains(&name) => {
            let positional = static_args.iter().enumerate().find_map(|(index, value)| {
                let value = value.clone()?;
                (!value.starts_with('-') && !value.contains('=')).then_some((index, value))
            });
            match positional {
                Some((index, inner_name)) => {
                    let (effect, mut reasons) =
                        classify_resolved(&inner_name, &arguments[index + 1..], depth);
                    reasons.insert(0, format!("wrapped by `{name}`"));
                    (effect, reasons)
                }
                None if name == "env" && arguments.iter().all(|argument| !argument.dynamic) => (
                    EffectClass::ReadOnly,
                    vec!["`env` prints the environment".to_owned()],
                ),
                None if name == "xargs" => (
                    EffectClass::ReadOnly,
                    vec!["`xargs` default utility is echo".to_owned()],
                ),
                None => (
                    EffectClass::DynamicUnknown,
                    vec![format!("`{name}` wraps a dynamic command")],
                ),
            }
        }
        name if SHELL_STATE_BUILTINS.contains(&name) => (
            EffectClass::ReadOnly,
            vec![format!("shell builtin `{name}` mutates only shell state")],
        ),
        name if READ_ONLY_COMMANDS.contains(&name) => (EffectClass::ReadOnly, Vec::new()),
        name if FILESYSTEM_WRITE_COMMANDS.contains(&name) => (
            EffectClass::FilesystemWrite,
            vec![format!("`{name}` writes the filesystem")],
        ),
        name if DESTRUCTIVE_COMMANDS.contains(&name) || name.starts_with("mkfs.") => (
            EffectClass::Destructive,
            vec![format!("`{name}` destroys data")],
        ),
        name if NETWORK_COMMANDS.contains(&name) => (
            EffectClass::NetworkAccess,
            vec![format!("`{name}` reaches the network")],
        ),
        name if PROCESS_CONTROL_COMMANDS.contains(&name) => (
            EffectClass::ProcessControl,
            vec![format!("`{name}` controls processes or services")],
        ),
        name if PRIVILEGED_COMMANDS.contains(&name) => (
            EffectClass::Privileged,
            vec![format!("`{name}` requires elevated privileges")],
        ),
        name if PACKAGE_MANAGERS.contains(&name) => (
            EffectClass::PackageManagement,
            vec![format!("`{name}` manages installed packages")],
        ),
        _ => (
            EffectClass::Unknown,
            vec![format!("`{name}` is not in the effect tables")],
        ),
    }
}

fn classify_command(
    executable: &ExecutableProvenance,
    arguments: &[ArgumentProvenance],
    redirections: &[RedirectionAnalysis],
    depth: usize,
) -> (EffectClass, Vec<String>) {
    let (mut effect, mut reasons) = match (&executable.kind, executable.resolved.as_deref()) {
        (ExecutableKind::Dynamic, _) => (
            EffectClass::DynamicUnknown,
            vec!["executable is computed at runtime".to_owned()],
        ),
        (ExecutableKind::None, _) => (EffectClass::ReadOnly, Vec::new()),
        (ExecutableKind::ScriptFunction, Some(name)) => (
            EffectClass::ReadOnly,
            vec![format!("call to script function `{name}`")],
        ),
        (ExecutableKind::RelativePath, Some(_)) => (
            EffectClass::Unknown,
            vec!["local script or binary content is not analyzed".to_owned()],
        ),
        (_, Some(name)) => classify_resolved(name, arguments, depth),
        (_, None) => (
            EffectClass::Unknown,
            vec!["executable could not be resolved".to_owned()],
        ),
    };
    if redirections.iter().any(|redirect| redirect.writes_file) {
        effect = max_effect(effect, EffectClass::FilesystemWrite);
        reasons.push("redirection writes a file".to_owned());
    }
    (effect, reasons)
}

/// Resolve calls to script-defined functions to the maximum effect of their
/// bodies, iterating to cover function-calls-function chains.
fn resolve_function_call_effects(commands: &mut [CommandAnalysis], functions: &BTreeSet<String>) {
    for _ in 0..3 {
        let mut body_effects: BTreeMap<String, EffectClass> = BTreeMap::new();
        for command in commands.iter() {
            if let Some(function) = &command.enclosing_function {
                let entry = body_effects
                    .entry(function.clone())
                    .or_insert(EffectClass::ReadOnly);
                *entry = max_effect(*entry, command.effect);
            }
        }
        let mut changed = false;
        for command in commands.iter_mut() {
            if command.executable.kind != ExecutableKind::ScriptFunction {
                continue;
            }
            let Some(name) = command.executable.resolved.as_deref() else {
                continue;
            };
            if !functions.contains(name) {
                continue;
            }
            let resolved = body_effects
                .get(name)
                .copied()
                .unwrap_or(EffectClass::ReadOnly);
            if resolved.severity() > command.effect.severity() {
                command.effect = resolved;
                command
                    .effect_reasons
                    .push(format!("function `{name}` body classified as {resolved:?}"));
                changed = true;
            }
        }
        if !changed {
            break;
        }
    }
}

// ---------------------------------------------------------------------------
// Analysis entry point
// ---------------------------------------------------------------------------

fn analyze_shell_script_at_depth(source: &str, depth: usize) -> Result<ShellScriptAnalysis> {
    let mut parser = TreeSitterParser::new();
    parser
        .set_language(&tree_sitter_bash::LANGUAGE.into())
        .context("loading Tree-sitter Bash grammar")?;
    let tree = parser
        .parse(source.as_bytes(), None)
        .context("Tree-sitter Bash returned no tree")?;
    let parse_valid = !tree.root_node().has_error();
    let mut diagnostics = if parse_valid {
        Vec::new()
    } else {
        crate::query_packs::error_diagnostics(&tree)
    };

    let mut functions = BTreeSet::new();
    collect_function_names(tree.root_node(), source.as_bytes(), &mut functions);
    let mut walker = Walker {
        source: source.as_bytes(),
        functions,
        commands: Vec::new(),
        pipelines: Vec::new(),
        substitutions: Vec::new(),
        assignments: Vec::new(),
        has_background_jobs: false,
        dropped_commands: 0,
        depth,
    };
    let state = WalkState {
        context: CommandContext::TopLevel,
        enclosing_function: None,
        pipeline_index: None,
        negated: false,
    };
    walker.visit(tree.root_node(), state);
    let functions = walker.functions;
    let mut commands = walker.commands;
    resolve_function_call_effects(&mut commands, &functions);

    let overall_effect = commands
        .iter()
        .map(|command| command.effect)
        .fold(EffectClass::ReadOnly, max_effect);
    let overall_effect = if parse_valid {
        overall_effect
    } else {
        // An unparseable script cannot be fully analyzed.
        max_effect(overall_effect, EffectClass::Unknown)
    };

    for command in &commands {
        if command.executable.kind == ExecutableKind::Dynamic {
            diagnostics.push(Diagnostic {
                message: "executable is computed at runtime".to_owned(),
                severity: "warning".to_owned(),
                start_byte: Some(command.start_byte),
                end_byte: Some(command.end_byte),
            });
        }
        if command.effect == EffectClass::Destructive
            && command.arguments.iter().any(|argument| argument.dynamic)
        {
            diagnostics.push(Diagnostic {
                message: "destructive command receives dynamically expanded arguments".to_owned(),
                severity: "warning".to_owned(),
                start_byte: Some(command.start_byte),
                end_byte: Some(command.end_byte),
            });
        }
    }
    if walker.has_background_jobs {
        diagnostics.push(Diagnostic {
            message: "script starts background jobs; process-tree capture is required".to_owned(),
            severity: "warning".to_owned(),
            start_byte: None,
            end_byte: None,
        });
    }
    if walker.dropped_commands > 0 {
        diagnostics.push(Diagnostic {
            message: format!(
                "command analysis truncated: {} dropped beyond the {MAX_ANALYZED_COMMANDS} budget",
                walker.dropped_commands
            ),
            severity: "warning".to_owned(),
            start_byte: None,
            end_byte: None,
        });
    }
    diagnostics.sort_by_key(|diagnostic| (diagnostic.start_byte, diagnostic.end_byte));

    Ok(ShellScriptAnalysis {
        engine: SHELL_SEMANTICS_ENGINE.to_owned(),
        engine_version: crate::TREE_SITTER_ENGINE_VERSION.to_owned(),
        semantics_version: SHELL_SEMANTICS_VERSION.to_owned(),
        parse_valid,
        commands,
        pipelines: walker.pipelines,
        substitutions: walker.substitutions,
        assignments: walker.assignments,
        functions: functions.into_iter().collect(),
        has_background_jobs: walker.has_background_jobs,
        overall_effect,
        diagnostics,
        truncated: walker.dropped_commands > 0,
    })
}

/// Analyze one shell script: full command topology, provenance, and
/// fail-closed effect classification. Never executes anything.
pub fn analyze_shell_script(source: &str) -> Result<ShellScriptAnalysis> {
    analyze_shell_script_at_depth(source, 0)
}

// ---------------------------------------------------------------------------
// Policy: sandbox, limits, capture, reconciliation
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct SandboxSpec {
    /// Roots the executor must confine writes to; empty means no write is
    /// permitted at all.
    pub allowed_write_roots: Vec<String>,
    pub network_allowed: bool,
    /// Environment variables passed through; everything else is dropped.
    pub environment_allowlist: Vec<String>,
    /// Truthful enforcement status: this daemon does not execute shell, so
    /// enforcement is the executor's obligation, stated as such.
    pub enforcement: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ResourceLimits {
    pub wall_clock_ms: u64,
    pub max_stdout_bytes: u64,
    pub max_stderr_bytes: u64,
    pub max_processes: u32,
}

impl Default for ResourceLimits {
    fn default() -> Self {
        Self {
            wall_clock_ms: 120_000,
            max_stdout_bytes: 4 * 1024 * 1024,
            max_stderr_bytes: 1024 * 1024,
            max_processes: 64,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ProcessCaptureSpec {
    /// Run the script in its own process group so the whole tree is
    /// addressable.
    pub new_process_group: bool,
    /// Record pid, parent pid, and process group of every spawned process.
    pub capture_children: bool,
    /// Kill the entire process group when limits are exceeded.
    pub kill_process_group_on_limit: bool,
}

impl Default for ProcessCaptureSpec {
    fn default() -> Self {
        Self {
            new_process_group: true,
            capture_children: true,
            kill_process_group_on_limit: true,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ReconciliationPlan {
    /// Files the analysis predicts the script may write, from static
    /// redirection targets and static write-command arguments.
    pub predicted_writes: Vec<String>,
    /// `static` when every write target was statically known, `partial` when
    /// some were dynamic, `unknown` when the script has unknown effects.
    pub certainty: String,
    /// The executor must scan for changed files after the run and reconcile.
    pub require_post_run_scan: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ReconciliationReport {
    pub predicted_writes: Vec<String>,
    pub observed_changes: Vec<String>,
    /// Observed changes the analysis did not predict.
    pub unpredicted_changes: Vec<String>,
    /// Predicted writes that did not materialize.
    pub predicted_but_unchanged: Vec<String>,
    /// No unpredicted changes were observed.
    pub clean: bool,
}

fn predicted_writes(analysis: &ShellScriptAnalysis) -> (Vec<String>, String) {
    let mut predictions = BTreeSet::new();
    let mut partial = false;
    for command in &analysis.commands {
        for redirect in &command.redirections {
            if !redirect.writes_file {
                continue;
            }
            match redirect
                .target
                .as_ref()
                .and_then(|target| target.static_value.clone())
            {
                Some(target) => {
                    predictions.insert(target);
                }
                None => partial = true,
            }
        }
        let name = command.executable.resolved.as_deref();
        if matches!(name, Some("tee")) {
            for argument in &command.arguments {
                match &argument.static_value {
                    Some(value) if !value.starts_with('-') => {
                        predictions.insert(value.clone());
                    }
                    Some(_) => {}
                    None => partial = true,
                }
            }
        }
        if matches!(name, Some("touch") | Some("mkdir")) {
            for argument in &command.arguments {
                match &argument.static_value {
                    Some(value) if !value.starts_with('-') => {
                        predictions.insert(value.clone());
                    }
                    Some(_) => {}
                    None => partial = true,
                }
            }
        }
        if matches!(name, Some("cp") | Some("mv")) {
            let positional = command
                .arguments
                .iter()
                .filter(|argument| {
                    argument
                        .static_value
                        .as_deref()
                        .is_none_or(|value| !value.starts_with('-'))
                })
                .collect::<Vec<_>>();
            match positional
                .last()
                .and_then(|argument| argument.static_value.clone())
            {
                Some(destination) if positional.len() >= 2 => {
                    predictions.insert(destination);
                }
                _ => partial = true,
            }
        }
    }
    let certainty = if analysis.overall_effect.severity() >= 4 {
        "unknown"
    } else if partial {
        "partial"
    } else {
        "static"
    };
    (predictions.into_iter().collect(), certainty.to_owned())
}

/// Compare predicted writes against the changed files the executor observed.
pub fn reconcile_changed_files(
    plan: &ReconciliationPlan,
    observed: &[String],
) -> ReconciliationReport {
    let predicted: BTreeSet<&String> = plan.predicted_writes.iter().collect();
    let observed_set: BTreeSet<&String> = observed.iter().collect();
    let unpredicted_changes = observed
        .iter()
        .filter(|path| !predicted.contains(path))
        .cloned()
        .collect::<Vec<_>>();
    let predicted_but_unchanged = plan
        .predicted_writes
        .iter()
        .filter(|path| !observed_set.contains(path))
        .cloned()
        .collect::<Vec<_>>();
    ReconciliationReport {
        predicted_writes: plan.predicted_writes.clone(),
        observed_changes: observed.to_vec(),
        clean: unpredicted_changes.is_empty(),
        unpredicted_changes,
        predicted_but_unchanged,
    }
}

// ---------------------------------------------------------------------------
// Optional external probes (ShellCheck, shfmt)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ProbeFinding {
    pub level: String,
    pub code: Option<String>,
    pub message: String,
    pub line: Option<u64>,
    pub column: Option<u64>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ProbeOutcome {
    pub tool: String,
    pub license: String,
    /// One of `ran`, `unavailable`, `failed`, `timed_out`, `skipped`.
    pub status: String,
    pub version: Option<String>,
    pub findings: Vec<ProbeFinding>,
    pub output_truncated: bool,
    pub detail: Option<String>,
}

impl ProbeOutcome {
    fn skipped(tool: &str, license: &str) -> Self {
        Self {
            tool: tool.to_owned(),
            license: license.to_owned(),
            status: "skipped".to_owned(),
            version: None,
            findings: Vec::new(),
            output_truncated: false,
            detail: None,
        }
    }

    fn unavailable(tool: &str, license: &str) -> Self {
        Self {
            status: "unavailable".to_owned(),
            detail: Some(format!("`{tool}` was not found on the probe search path")),
            ..Self::skipped(tool, license)
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ExternalProbeReport {
    pub shellcheck: ProbeOutcome,
    pub shfmt: ProbeOutcome,
}

impl ExternalProbeReport {
    pub fn skipped() -> Self {
        Self {
            shellcheck: ProbeOutcome::skipped("shellcheck", SHELLCHECK_LICENSE),
            shfmt: ProbeOutcome::skipped("shfmt", SHFMT_LICENSE),
        }
    }
}

const SHELLCHECK_LICENSE: &str =
    "GPL-3.0-or-later; optional external probe, never linked or bundled";
const SHFMT_LICENSE: &str =
    "BSD-3-Clause (mvdan.cc/sh); optional external probe, never linked or bundled";

fn is_executable_file(path: &Path) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        path.metadata()
            .map(|metadata| metadata.is_file() && metadata.permissions().mode() & 0o111 != 0)
            .unwrap_or(false)
    }
    #[cfg(not(unix))]
    {
        path.is_file()
    }
}

fn locate_executable(name: &str, search_path: Option<&str>) -> Option<PathBuf> {
    let path_value = match search_path {
        Some(value) => value.to_owned(),
        None => std::env::var("PATH").ok()?,
    };
    std::env::split_paths(&path_value)
        .map(|directory| directory.join(name))
        .find(|candidate| is_executable_file(candidate))
}

struct BoundedRun {
    exit_code: Option<i32>,
    timed_out: bool,
    stdout: Vec<u8>,
    truncated: bool,
}

fn capped_reader(mut source: impl Read, cap: usize) -> (Vec<u8>, bool) {
    let mut buffer = Vec::new();
    let mut chunk = [0u8; 8192];
    let mut truncated = false;
    loop {
        match source.read(&mut chunk) {
            Ok(0) => break,
            Ok(read) => {
                if buffer.len() < cap {
                    let take = read.min(cap - buffer.len());
                    buffer.extend_from_slice(&chunk[..take]);
                    if take < read {
                        truncated = true;
                    }
                } else {
                    truncated = true;
                }
            }
            Err(_) => break,
        }
    }
    (buffer, truncated)
}

fn run_bounded(
    program: &Path,
    args: &[&str],
    stdin_bytes: &[u8],
    timeout_ms: u64,
) -> Result<BoundedRun> {
    let mut child = Command::new(program)
        .args(args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .with_context(|| format!("spawning probe {}", program.display()))?;
    let mut stdin = child.stdin.take().context("probe stdin unavailable")?;
    let stdin_payload = stdin_bytes.to_vec();
    let writer = std::thread::spawn(move || {
        use std::io::Write;
        let _ = stdin.write_all(&stdin_payload);
        drop(stdin);
    });
    let stdout = child.stdout.take().context("probe stdout unavailable")?;
    let reader = std::thread::spawn(move || capped_reader(stdout, PROBE_OUTPUT_CAP_BYTES));

    let deadline = Instant::now() + Duration::from_millis(timeout_ms);
    let mut timed_out = false;
    let exit_status = loop {
        match child.try_wait()? {
            Some(status) => break Some(status),
            None if Instant::now() >= deadline => {
                let _ = child.kill();
                timed_out = true;
                break child.wait().ok();
            }
            None => std::thread::sleep(Duration::from_millis(10)),
        }
    };
    let _ = writer.join();
    let (stdout, truncated) = reader.join().unwrap_or_default();
    Ok(BoundedRun {
        exit_code: exit_status.and_then(|status| status.code()),
        timed_out,
        stdout,
        truncated,
    })
}

fn probe_version(program: &Path, timeout_ms: u64) -> Option<String> {
    let run = run_bounded(program, &["--version"], b"", timeout_ms).ok()?;
    let text = String::from_utf8_lossy(&run.stdout);
    text.lines()
        .map(str::trim)
        .find(|line| !line.is_empty())
        .map(|line| {
            line.strip_prefix("version:")
                .map(str::trim)
                .unwrap_or(line)
                .to_owned()
        })
}

/// Run ShellCheck against the script when it is present on the search path.
/// Absence degrades truthfully to `unavailable`; it is never an error.
pub fn shellcheck_probe(source: &str, search_path: Option<&str>, timeout_ms: u64) -> ProbeOutcome {
    let Some(program) = locate_executable("shellcheck", search_path) else {
        return ProbeOutcome::unavailable("shellcheck", SHELLCHECK_LICENSE);
    };
    let version = probe_version(&program, timeout_ms);
    let run = match run_bounded(
        &program,
        &["--format=json", "--shell=bash", "-"],
        source.as_bytes(),
        timeout_ms,
    ) {
        Ok(run) => run,
        Err(error) => {
            return ProbeOutcome {
                status: "failed".to_owned(),
                version,
                detail: Some(format!("{error:#}")),
                ..ProbeOutcome::skipped("shellcheck", SHELLCHECK_LICENSE)
            };
        }
    };
    if run.timed_out {
        return ProbeOutcome {
            status: "timed_out".to_owned(),
            version,
            ..ProbeOutcome::skipped("shellcheck", SHELLCHECK_LICENSE)
        };
    }
    let findings = serde_json::from_slice::<serde_json::Value>(&run.stdout)
        .ok()
        .and_then(|value| value.as_array().cloned())
        .map(|entries| {
            entries
                .iter()
                .map(|entry| ProbeFinding {
                    level: entry
                        .get("level")
                        .and_then(serde_json::Value::as_str)
                        .unwrap_or("unknown")
                        .to_owned(),
                    code: entry
                        .get("code")
                        .and_then(serde_json::Value::as_u64)
                        .map(|code| format!("SC{code}")),
                    message: entry
                        .get("message")
                        .and_then(serde_json::Value::as_str)
                        .unwrap_or_default()
                        .to_owned(),
                    line: entry.get("line").and_then(serde_json::Value::as_u64),
                    column: entry.get("column").and_then(serde_json::Value::as_u64),
                })
                .collect::<Vec<_>>()
        });
    match findings {
        Some(findings) => ProbeOutcome {
            tool: "shellcheck".to_owned(),
            license: SHELLCHECK_LICENSE.to_owned(),
            status: "ran".to_owned(),
            version,
            findings,
            output_truncated: run.truncated,
            detail: None,
        },
        None => ProbeOutcome {
            status: "failed".to_owned(),
            version,
            detail: Some(format!(
                "shellcheck produced unparseable output (exit code {:?})",
                run.exit_code
            )),
            output_truncated: run.truncated,
            ..ProbeOutcome::skipped("shellcheck", SHELLCHECK_LICENSE)
        },
    }
}

/// Run `shfmt -d` against the script when it is present on the search path.
pub fn shfmt_probe(source: &str, search_path: Option<&str>, timeout_ms: u64) -> ProbeOutcome {
    let Some(program) = locate_executable("shfmt", search_path) else {
        return ProbeOutcome::unavailable("shfmt", SHFMT_LICENSE);
    };
    let version = probe_version(&program, timeout_ms);
    let run = match run_bounded(&program, &["-d"], source.as_bytes(), timeout_ms) {
        Ok(run) => run,
        Err(error) => {
            return ProbeOutcome {
                status: "failed".to_owned(),
                version,
                detail: Some(format!("{error:#}")),
                ..ProbeOutcome::skipped("shfmt", SHFMT_LICENSE)
            };
        }
    };
    if run.timed_out {
        return ProbeOutcome {
            status: "timed_out".to_owned(),
            version,
            ..ProbeOutcome::skipped("shfmt", SHFMT_LICENSE)
        };
    }
    let findings = if run.stdout.is_empty() {
        Vec::new()
    } else {
        vec![ProbeFinding {
            level: "style".to_owned(),
            code: None,
            message: format!(
                "formatting differs from shfmt output:\n{}",
                String::from_utf8_lossy(&run.stdout)
            ),
            line: None,
            column: None,
        }]
    };
    ProbeOutcome {
        tool: "shfmt".to_owned(),
        license: SHFMT_LICENSE.to_owned(),
        status: "ran".to_owned(),
        version,
        findings,
        output_truncated: run.truncated,
        detail: None,
    }
}

/// Probe execution mode for [`shell_approval_preview`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProbeMode {
    /// Do not spawn anything; the report records `skipped`.
    Skip,
    /// Invoke the tools when present; absence degrades to `unavailable`.
    Run,
}

// ---------------------------------------------------------------------------
// Approval preview, grants, audit receipts
// ---------------------------------------------------------------------------

/// Policy knobs supplied by the caller. This struct is configuration, not
/// exposure: it never appears in previews or receipts.
#[derive(Debug, Clone)]
pub struct ShellPolicyConfig {
    pub max_script_bytes: usize,
    pub allow_network: bool,
    /// Roots the sandbox permits writes under; usually the workspace root.
    pub allowed_write_roots: Vec<String>,
    pub environment_allowlist: Vec<String>,
    pub limits: ResourceLimits,
    pub capture: ProcessCaptureSpec,
    /// Require approval even for read-only scripts.
    pub require_approval_for_all: bool,
    /// Override the probe search path (tests inject fake tools through this).
    pub probe_search_path: Option<String>,
    pub probe_timeout_ms: u64,
}

impl Default for ShellPolicyConfig {
    fn default() -> Self {
        Self {
            max_script_bytes: 256 * 1024,
            allow_network: false,
            allowed_write_roots: Vec::new(),
            environment_allowlist: ["PATH", "HOME", "LANG", "LC_ALL", "TERM", "TMPDIR"]
                .iter()
                .map(|name| (*name).to_owned())
                .collect(),
            limits: ResourceLimits::default(),
            capture: ProcessCaptureSpec::default(),
            require_approval_for_all: false,
            probe_search_path: None,
            probe_timeout_ms: 10_000,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ShellApprovalPreview {
    pub schema_version: String,
    pub preview_id: String,
    pub created_at_unix_ms: u64,
    /// Hash of the exact raw script; approval binds to it without exposing
    /// unredacted content.
    pub script_sha256: String,
    pub redacted_script: String,
    pub redaction_count: usize,
    pub analysis: ShellScriptAnalysis,
    pub overall_effect: EffectClass,
    pub required_risk_level: String,
    pub requires_approval: bool,
    /// Non-empty refusals deny execution even with approval.
    pub refusals: Vec<String>,
    pub sandbox: SandboxSpec,
    pub limits: ResourceLimits,
    pub capture: ProcessCaptureSpec,
    pub reconciliation: ReconciliationPlan,
    pub probes: ExternalProbeReport,
    pub digest: String,
}

fn sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    format!("{:x}", hasher.finalize())
}

fn unix_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}

fn preview_digest(preview: &ShellApprovalPreview) -> Result<String> {
    let mut payload = preview.clone();
    payload.digest = String::new();
    Ok(sha256_hex(&serde_json::to_vec(&payload)?))
}

fn redact_analysis(analysis: &mut ShellScriptAnalysis) -> usize {
    let mut count = 0usize;
    let mut redact = |text: &mut String| {
        let redacted = soleaux_redaction::redact_text(text);
        count = count.saturating_add(redacted.count);
        *text = redacted.value;
    };
    for command in &mut analysis.commands {
        redact(&mut command.source);
        redact(&mut command.executable.text);
        for argument in &mut command.arguments {
            redact(&mut argument.text);
            if let Some(value) = argument.static_value.as_mut() {
                redact(value);
            }
        }
        for redirection in &mut command.redirections {
            if let Some(target) = redirection.target.as_mut() {
                redact(&mut target.text);
                if let Some(value) = target.static_value.as_mut() {
                    redact(value);
                }
            }
        }
    }
    for assignment in &mut analysis.assignments {
        redact(&mut assignment.value_text);
    }
    count
}

/// Build the approval preview for one script: analysis, redaction, policy
/// decision, sandbox/limits/capture contract, reconciliation plan, optional
/// external probes, and a content digest that approvals bind to.
pub fn shell_approval_preview(
    source: &str,
    config: &ShellPolicyConfig,
    probe_mode: ProbeMode,
) -> Result<ShellApprovalPreview> {
    let mut refusals = Vec::new();
    if source.len() > config.max_script_bytes {
        refusals.push(format!(
            "script is {} bytes; the policy ceiling is {}",
            source.len(),
            config.max_script_bytes
        ));
    }
    let mut analysis = analyze_shell_script(source)?;
    if !analysis.parse_valid {
        refusals
            .push("script does not parse as Bash; execution policy cannot be evaluated".to_owned());
    }
    let overall_effect = analysis.overall_effect;
    if overall_effect == EffectClass::NetworkAccess && !config.allow_network {
        refusals.push("script reaches the network and network access is disabled".to_owned());
    }
    let requires_approval =
        config.require_approval_for_all || overall_effect.severity() > 0 || analysis.truncated;
    let (predictions, certainty) = predicted_writes(&analysis);
    let reconciliation = ReconciliationPlan {
        predicted_writes: predictions,
        certainty,
        require_post_run_scan: overall_effect.severity() > 0,
    };
    let probes = match probe_mode {
        ProbeMode::Skip => ExternalProbeReport::skipped(),
        ProbeMode::Run => ExternalProbeReport {
            shellcheck: shellcheck_probe(
                source,
                config.probe_search_path.as_deref(),
                config.probe_timeout_ms,
            ),
            shfmt: shfmt_probe(
                source,
                config.probe_search_path.as_deref(),
                config.probe_timeout_ms,
            ),
        },
    };
    let script_sha256 = sha256_hex(source.as_bytes());
    let redacted_script = soleaux_redaction::redact_text(source);
    let analysis_redactions = redact_analysis(&mut analysis);
    let mut preview = ShellApprovalPreview {
        schema_version: SHELL_APPROVAL_SCHEMA_VERSION.to_owned(),
        preview_id: Uuid::now_v7().to_string(),
        created_at_unix_ms: unix_ms(),
        script_sha256,
        redacted_script: redacted_script.value,
        redaction_count: redacted_script.count.saturating_add(analysis_redactions),
        analysis,
        overall_effect,
        required_risk_level: overall_effect.required_risk_level().to_owned(),
        requires_approval,
        refusals,
        sandbox: SandboxSpec {
            allowed_write_roots: config.allowed_write_roots.clone(),
            network_allowed: config.allow_network
                && matches!(
                    overall_effect,
                    EffectClass::NetworkAccess | EffectClass::PackageManagement
                ),
            environment_allowlist: config.environment_allowlist.clone(),
            enforcement: "required-by-executor".to_owned(),
        },
        limits: config.limits.clone(),
        capture: config.capture.clone(),
        reconciliation,
        probes,
        digest: String::new(),
    };
    preview.digest = preview_digest(&preview)?;
    Ok(preview)
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ShellApproval {
    pub approved_by: String,
    pub preview_digest: String,
    pub approved_at_unix_ms: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ShellExecutionGrant {
    pub schema_version: String,
    pub preview_id: String,
    pub preview_digest: String,
    pub script_sha256: String,
    pub sandbox: SandboxSpec,
    pub limits: ResourceLimits,
    pub capture: ProcessCaptureSpec,
    pub approval: Option<ShellApproval>,
    pub granted_at_unix_ms: u64,
}

/// Authorize execution of a previewed script. Fails closed: refusals deny
/// even with approval, a tampered preview is rejected, and any effect above
/// read-only without a digest-bound approval is rejected.
pub fn authorize_execution(
    preview: &ShellApprovalPreview,
    approval: Option<&ShellApproval>,
) -> Result<ShellExecutionGrant> {
    let expected = preview_digest(preview)?;
    if preview.digest != expected {
        bail!("preview digest does not match its content");
    }
    if !preview.refusals.is_empty() {
        bail!(
            "execution refused by policy: {}",
            preview.refusals.join("; ")
        );
    }
    if preview.requires_approval {
        let Some(approval) = approval else {
            bail!(
                "effect class {:?} requires explicit approval; none was provided",
                preview.overall_effect
            );
        };
        if approval.preview_digest != preview.digest {
            bail!("approval is bound to a different preview digest");
        }
    }
    Ok(ShellExecutionGrant {
        schema_version: SHELL_GRANT_SCHEMA_VERSION.to_owned(),
        preview_id: preview.preview_id.clone(),
        preview_digest: preview.digest.clone(),
        script_sha256: preview.script_sha256.clone(),
        sandbox: preview.sandbox.clone(),
        limits: preview.limits.clone(),
        capture: preview.capture.clone(),
        approval: approval.cloned(),
        granted_at_unix_ms: unix_ms(),
    })
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct CapturedProcess {
    pub pid: u32,
    pub parent_pid: u32,
    pub process_group: u32,
    pub command: String,
}

/// What the executor observed; supplied when building the audit receipt.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ShellExecutionOutcome {
    pub exit_code: Option<i32>,
    pub timed_out: bool,
    pub stdout_sha256: String,
    pub stderr_sha256: String,
    pub stdout_truncated: bool,
    pub stderr_truncated: bool,
    pub observed_changed_files: Vec<String>,
    pub process_tree: Vec<CapturedProcess>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ShellAuditReceipt {
    pub schema_version: String,
    pub receipt_id: String,
    pub preview_id: String,
    pub preview_digest: String,
    pub script_sha256: String,
    pub granted: bool,
    pub approval: Option<ShellApproval>,
    pub outcome: ShellExecutionOutcome,
    pub reconciliation: ReconciliationReport,
    pub created_at_unix_ms: u64,
    pub digest: String,
}

fn receipt_digest(receipt: &ShellAuditReceipt) -> Result<String> {
    let mut payload = receipt.clone();
    payload.digest = String::new();
    Ok(sha256_hex(&serde_json::to_vec(&payload)?))
}

/// Build the audit receipt binding the preview, the grant, the observed
/// outcome, and the changed-file reconciliation. Process command lines are
/// redacted before they enter the receipt.
pub fn build_audit_receipt(
    preview: &ShellApprovalPreview,
    grant: Option<&ShellExecutionGrant>,
    mut outcome: ShellExecutionOutcome,
) -> Result<ShellAuditReceipt> {
    for process in &mut outcome.process_tree {
        process.command = soleaux_redaction::redact_text(&process.command).value;
    }
    let reconciliation =
        reconcile_changed_files(&preview.reconciliation, &outcome.observed_changed_files);
    let mut receipt = ShellAuditReceipt {
        schema_version: SHELL_AUDIT_SCHEMA_VERSION.to_owned(),
        receipt_id: Uuid::now_v7().to_string(),
        preview_id: preview.preview_id.clone(),
        preview_digest: preview.digest.clone(),
        script_sha256: preview.script_sha256.clone(),
        granted: grant.is_some(),
        approval: grant.and_then(|grant| grant.approval.clone()),
        outcome,
        reconciliation,
        created_at_unix_ms: unix_ms(),
        digest: String::new(),
    };
    receipt.digest = receipt_digest(&receipt)?;
    Ok(receipt)
}

/// Recompute and check an audit receipt's digest.
pub fn verify_audit_receipt(receipt: &ShellAuditReceipt) -> Result<bool> {
    Ok(receipt.digest == receipt_digest(receipt)?)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn analyze(source: &str) -> ShellScriptAnalysis {
        analyze_shell_script(source).expect("analysis")
    }

    #[test]
    fn read_only_pipeline_is_classified_read_only() {
        let analysis = analyze("cat Cargo.toml | grep name | wc -l\n");
        assert!(analysis.parse_valid);
        assert_eq!(analysis.overall_effect, EffectClass::ReadOnly);
        assert_eq!(analysis.pipelines.len(), 1);
        assert_eq!(analysis.pipelines[0].stage_count, 3);
        assert_eq!(analysis.commands.len(), 3);
        assert!(
            analysis
                .commands
                .iter()
                .all(|command| command.pipeline_index == Some(0))
        );
    }

    #[test]
    fn package_managers_are_classified_as_package_management() {
        let analysis = analyze("pip install requests\n");
        assert_eq!(analysis.overall_effect, EffectClass::PackageManagement);
        let analysis = analyze("brew upgrade\n");
        assert_eq!(analysis.overall_effect, EffectClass::PackageManagement);
    }

    #[test]
    fn dynamic_executable_fails_closed() {
        let analysis = analyze("$CMD --flag\n");
        assert_eq!(analysis.commands.len(), 1);
        assert_eq!(
            analysis.commands[0].executable.kind,
            ExecutableKind::Dynamic
        );
        assert_eq!(analysis.overall_effect, EffectClass::DynamicUnknown);
    }

    #[test]
    fn write_redirect_escalates_read_only_command() {
        let analysis = analyze("echo hello > greeting.txt\n");
        assert_eq!(analysis.overall_effect, EffectClass::FilesystemWrite);
        let command = &analysis.commands[0];
        assert_eq!(command.redirections.len(), 1);
        assert!(command.redirections[0].writes_file);
        assert_eq!(
            command.redirections[0]
                .target
                .as_ref()
                .and_then(|target| target.static_value.as_deref()),
            Some("greeting.txt")
        );
    }

    #[test]
    fn fd_duplication_is_not_a_file_write() {
        let analysis = analyze("ls missing 2>&1\n");
        let command = &analysis.commands[0];
        assert_eq!(command.redirections.len(), 1);
        assert_eq!(command.redirections[0].kind, "duplicate_fd");
        assert!(!command.redirections[0].writes_file);
        assert_eq!(analysis.overall_effect, EffectClass::ReadOnly);
    }

    #[test]
    fn command_substitution_contents_are_analyzed() {
        let analysis = analyze("echo \"today is $(rm -rf /tmp/x)\"\n");
        assert!(
            analysis
                .substitutions
                .iter()
                .any(|substitution| substitution.kind == "command")
        );
        assert!(analysis.commands.iter().any(|command| {
            command.executable.resolved.as_deref() == Some("rm")
                && command.context == CommandContext::CommandSubstitution
        }));
        assert_eq!(analysis.overall_effect, EffectClass::Destructive);
    }

    #[test]
    fn nested_shell_c_payload_is_classified() {
        let analysis = analyze("bash -c 'rm -rf build'\n");
        assert_eq!(analysis.overall_effect, EffectClass::Destructive);
        let analysis = analyze("bash -c \"$PAYLOAD\"\n");
        assert_eq!(analysis.overall_effect, EffectClass::DynamicUnknown);
    }

    #[test]
    fn sudo_combines_privilege_with_wrapped_effect() {
        let analysis = analyze("sudo rm -rf /var/log\n");
        assert_eq!(analysis.overall_effect, EffectClass::Destructive);
        let command = &analysis.commands[0];
        assert!(
            command
                .effect_reasons
                .iter()
                .any(|reason| reason.contains("escalates"))
        );
    }

    #[test]
    fn git_subcommands_split_read_and_network() {
        assert_eq!(
            analyze("git status\n").overall_effect,
            EffectClass::ReadOnly
        );
        assert_eq!(
            analyze("git push origin main\n").overall_effect,
            EffectClass::NetworkAccess
        );
        assert_eq!(
            analyze("git clean -fd\n").overall_effect,
            EffectClass::Destructive
        );
    }

    #[test]
    fn function_call_inherits_body_effect() {
        let script = "cleanup() {\n  rm -rf target\n}\ncleanup\n";
        let analysis = analyze(script);
        let call = analysis
            .commands
            .iter()
            .find(|command| {
                command.executable.kind == ExecutableKind::ScriptFunction
                    && command.context == CommandContext::TopLevel
            })
            .expect("function call");
        assert_eq!(call.effect, EffectClass::Destructive);
        assert_eq!(analysis.overall_effect, EffectClass::Destructive);
    }

    #[test]
    fn heredoc_and_herestring_are_input_redirections() {
        let script = "cat <<EOF\nhello $(hostname)\nEOF\ncat <<< \"inline\"\n";
        let analysis = analyze(script);
        assert!(analysis.commands.iter().any(|command| {
            command
                .redirections
                .iter()
                .any(|redirect| redirect.kind == "heredoc" && !redirect.writes_file)
        }));
        assert!(analysis.commands.iter().any(|command| {
            command
                .redirections
                .iter()
                .any(|redirect| redirect.kind == "herestring")
        }));
        // The substitution inside the unquoted heredoc body executes.
        assert!(
            analysis
                .commands
                .iter()
                .any(|command| command.executable.resolved.as_deref() == Some("hostname"))
        );
    }

    #[test]
    fn unknown_command_fails_closed_to_approval() {
        let preview = shell_approval_preview(
            "totally-unknown-binary --do-things\n",
            &ShellPolicyConfig::default(),
            ProbeMode::Skip,
        )
        .expect("preview");
        assert_eq!(preview.overall_effect, EffectClass::Unknown);
        assert!(preview.requires_approval);
        assert!(authorize_execution(&preview, None).is_err());
    }

    #[test]
    fn background_jobs_are_detected() {
        let analysis = analyze("sleep 60 &\n");
        assert!(analysis.has_background_jobs);
    }
}
