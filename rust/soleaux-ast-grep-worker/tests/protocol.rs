//! Behavior tests for the JSONL protocol and the structural op, exercised
//! through the public `handle_frame`/`read_frame` surface.

use base64::Engine as _;
use base64::engine::general_purpose::STANDARD as BASE64;
use serde_json::{Value, json};
use soleaux_ast_grep_worker::{Frame, MAX_FRAME_BYTES, Outcome, handle_frame, read_frame};

fn reply(request: &Value) -> Value {
    match handle_frame(request.to_string().as_bytes()) {
        Outcome::Reply(value) => value,
        Outcome::Shutdown(_) => panic!("unexpected shutdown outcome"),
    }
}

fn file(path: &str, source: &str) -> Value {
    json!({"path": path, "content_b64": BASE64.encode(source)})
}

fn apply_edit(source: &str, edit: &Value) -> String {
    let start = edit["byte_start"].as_u64().expect("byte_start") as usize;
    let end = edit["byte_end"].as_u64().expect("byte_end") as usize;
    let inserted = edit["inserted_text"].as_str().expect("inserted_text");
    format!("{}{}{}", &source[..start], inserted, &source[end..])
}

#[test]
fn ping_reports_engine_identity() {
    let response = reply(&json!({"id": 7, "op": "ping"}));
    assert_eq!(
        response,
        json!({
            "id": 7,
            "ok": true,
            "engine": "rust",
            "engine_version": "0.44.1",
            "capabilities": ["soleaux.structural/v1"],
        })
    );
}

#[test]
fn shutdown_acknowledges_then_exits() {
    match handle_frame(br#"{"id": 9, "op": "shutdown"}"#) {
        Outcome::Shutdown(value) => assert_eq!(value, json!({"id": 9, "ok": true})),
        Outcome::Reply(_) => panic!("shutdown must produce a shutdown outcome"),
    }
}

#[test]
fn malformed_frame_is_a_protocol_error() {
    let response = reply(&Value::Null);
    // `null` is valid JSON but not a request object: missing op.
    assert_eq!(response["error"]["type"], "protocol");

    let Outcome::Reply(response) = handle_frame(b"{this is not json") else {
        panic!("malformed frame must reply");
    };
    assert!(response["id"].is_null());
    assert_eq!(response["error"]["type"], "protocol");

    let response = reply(&json!({"id": 3, "op": "florble"}));
    assert_eq!(response["id"], 3);
    assert_eq!(response["error"]["type"], "protocol");
}

#[test]
fn unsupported_language_is_typed() {
    let response = reply(&json!({
        "id": 11,
        "op": "structural",
        "language": "Klingon",
        "matcher": {"kind": "pattern", "pattern": "f($A)"},
        "fix": null,
        "transforms": null,
        "files": [file("a.ts", "f(1);")],
        "want": ["findings"],
        "limits": {"max_findings": 10, "max_capture_chars": 200, "max_preview_chars": 200},
    }));
    assert_eq!(response["id"], 11);
    assert_eq!(response["error"]["type"], "unsupported_language");
}

#[test]
fn pattern_findings_report_utf8_byte_offsets() {
    // Line 0 carries a two-byte character before the match so byte offsets
    // and code-point columns diverge:
    //   "const π = fetchData();"
    //   bytes: "const " = 6, "π" = 2, " = " = 3 -> match starts at byte 11
    //   chars: "const " = 6, "π" = 1, " = " = 3 -> match starts at column 10
    //   "fetchData()" = 11 bytes -> byte_end 22, end_column 21
    //   line 0 = 23 bytes + newline -> line 1 starts at byte 24
    //   line 1 match starts at "const x = ".len() = 10 -> bytes 34..45
    let source = "const π = fetchData();\nconst x = fetchData();\n";
    assert_eq!(&source[11..22], "fetchData()");
    assert_eq!(&source[34..45], "fetchData()");
    let response = reply(&json!({
        "id": 21,
        "op": "structural",
        "language": "TypeScript",
        "matcher": {"kind": "pattern", "pattern": "fetchData()"},
        "fix": null,
        "transforms": null,
        "files": [file("unicode.ts", source)],
        "want": ["findings"],
        "limits": {"max_findings": 10, "max_capture_chars": 200, "max_preview_chars": 200},
    }));
    assert_eq!(response["id"], 21);
    assert_eq!(response["engine"], "rust");
    assert_eq!(response["engine_version"], "0.44.1");
    assert_eq!(response["truncated"], false);
    assert_eq!(response["errors"], json!([]));
    let findings = response["findings"].as_array().expect("findings");
    assert_eq!(findings.len(), 2);
    assert_eq!(
        findings[0],
        json!({
            "path": "unicode.ts",
            "byte_start": 11,
            "byte_end": 22,
            "start_line": 0,
            "start_column": 10,
            "end_line": 0,
            "end_column": 21,
            "text_preview": "fetchData()",
            "captures": [],
        })
    );
    assert_eq!(findings[1]["byte_start"], 34);
    assert_eq!(findings[1]["byte_end"], 45);
    assert_eq!(findings[1]["start_line"], 1);
    assert_eq!(findings[1]["start_column"], 10);
}

#[test]
fn rule_matcher_applies_constraints_and_extracts_captures() {
    let source = "f(\"hello\");\ng(1);\n";
    let request = json!({
        "id": 31,
        "op": "structural",
        "language": "Tsx",
        "matcher": {
            "kind": "rule",
            "rule": {"pattern": "$F($ARG)"},
            "constraints": {"ARG": {"kind": "string"}},
            "utils": {},
        },
        "fix": null,
        "transforms": null,
        "files": [file("calls.tsx", source)],
        "want": ["findings"],
        "limits": {"max_findings": 10, "max_capture_chars": 200, "max_preview_chars": 200},
    });
    let response = reply(&request);
    let findings = response["findings"].as_array().expect("findings");
    assert_eq!(findings.len(), 1, "constraint must reject g(1): {response}");
    assert_eq!(findings[0]["text_preview"], "f(\"hello\")");
    assert_eq!(
        findings[0]["captures"],
        json!([
            {"name": "ARG", "text": "\"hello\"", "byte_start": 2, "byte_end": 9},
            {"name": "F", "text": "f", "byte_start": 0, "byte_end": 1},
        ])
    );

    let mut truncated = request.clone();
    truncated["id"] = json!(32);
    truncated["limits"]["max_capture_chars"] = json!(3);
    let response = reply(&truncated);
    assert_eq!(response["findings"][0]["captures"][0]["text"], "\"he");
}

#[test]
fn max_findings_truncates_across_files() {
    let source = "f(1);\nf(2);\n";
    let mut request = json!({
        "id": 41,
        "op": "structural",
        "language": "TypeScript",
        "matcher": {"kind": "pattern", "pattern": "f($A)"},
        "fix": null,
        "transforms": null,
        "files": [file("a.ts", source), file("b.ts", source)],
        "want": ["findings"],
        "limits": {"max_findings": 3, "max_capture_chars": 200, "max_preview_chars": 200},
    });
    let response = reply(&request);
    let findings = response["findings"].as_array().expect("findings");
    assert_eq!(findings.len(), 3);
    assert_eq!(response["truncated"], true);
    assert_eq!(findings[1]["path"], "a.ts");
    assert_eq!(findings[2]["path"], "b.ts");

    request["id"] = json!(42);
    request["limits"]["max_findings"] = json!(10);
    let response = reply(&request);
    assert_eq!(response["findings"].as_array().expect("findings").len(), 4);
    assert_eq!(response["truncated"], false);
}

#[test]
fn template_fix_renders_single_var_substitution() {
    let source = "var a = 1;\n";
    let response = reply(&json!({
        "id": 51,
        "op": "structural",
        "language": "TypeScript",
        "matcher": {"kind": "pattern", "pattern": "var $A = $B"},
        "fix": {"text": "let $A = $B"},
        "transforms": null,
        "files": [file("vars.ts", source)],
        "want": ["findings", "edits"],
        "limits": {"max_findings": 10, "max_capture_chars": 200, "max_preview_chars": 200},
    }));
    let edits = response["edits"].as_array().expect("edits");
    assert_eq!(edits.len(), 1);
    assert_eq!(edits[0]["path"], "vars.ts");
    // The replaced range excludes the trailing `;` (upstream match-length
    // trimming), so applying the edit preserves the semicolon.
    assert_eq!(edits[0]["byte_start"], 0);
    assert_eq!(edits[0]["byte_end"], 9);
    assert_eq!(edits[0]["inserted_text"], "let a = 1");
    assert_eq!(apply_edit(source, &edits[0]), "let a = 1;\n");
}

#[test]
fn template_fix_renders_multi_var_substitution() {
    let source = "f(1, 2);\n";
    let response = reply(&json!({
        "id": 61,
        "op": "structural",
        "language": "TypeScript",
        "matcher": {"kind": "pattern", "pattern": "f($$$ARGS)"},
        "fix": {"text": "g($$$ARGS)"},
        "transforms": null,
        "files": [file("multi.ts", source)],
        "want": ["findings", "edits"],
        "limits": {"max_findings": 10, "max_capture_chars": 200, "max_preview_chars": 200},
    }));
    let findings = response["findings"].as_array().expect("findings");
    assert_eq!(
        findings[0]["captures"],
        json!([{"name": "ARGS", "text": "1, 2", "byte_start": 2, "byte_end": 6}])
    );
    let edits = response["edits"].as_array().expect("edits");
    assert_eq!(edits.len(), 1);
    assert_eq!(edits[0]["inserted_text"], "g(1, 2)");
    assert_eq!(apply_edit(source, &edits[0]), "g(1, 2);\n");
}

#[test]
fn transforms_reuse_the_upstream_machinery() {
    // Transforms route through ast-grep-config's `RuleCore`, which replaces
    // the full matched node (upstream YAML-rule fix semantics: no trailing
    // `;` trimming, unlike the bare-pattern path), so the `;` is consumed.
    let source = "var foo = 1;\n";
    let cases = [
        (
            json!({"NAME": {"kind": "convert", "source": "$A", "to_case": "upperCase"}}),
            "let $NAME = $B;",
            "let FOO = 1;\n",
        ),
        (
            json!({"SUB": {"kind": "substring", "source": "$A", "start_char": 1}}),
            "let $SUB = $B;",
            "let oo = 1;\n",
        ),
        (
            json!({"R": {"kind": "replace", "source": "$A", "replace": "o+", "by": "0"}}),
            "let $R = $B;",
            "let f0 = 1;\n",
        ),
    ];
    for (index, (transforms, template, expected)) in cases.into_iter().enumerate() {
        let response = reply(&json!({
            "id": 70 + index,
            "op": "structural",
            "language": "TypeScript",
            "matcher": {"kind": "pattern", "pattern": "var $A = $B"},
            "fix": {"text": template},
            "transforms": transforms,
            "files": [file("t.ts", source)],
            "want": ["findings", "edits"],
            "limits": {"max_findings": 10, "max_capture_chars": 200, "max_preview_chars": 200},
        }));
        let edits = response["edits"].as_array().expect("edits");
        assert_eq!(edits.len(), 1, "case {index}: {response}");
        assert_eq!(apply_edit(source, &edits[0]), expected, "case {index}");
    }
}

#[test]
fn unsupported_transform_kind_is_typed() {
    let response = reply(&json!({
        "id": 81,
        "op": "structural",
        "language": "TypeScript",
        "matcher": {"kind": "pattern", "pattern": "var $A = $B"},
        "fix": {"text": "let $X = $B"},
        "transforms": {"X": {"kind": "rewrite", "source": "$A"}},
        "files": [file("t.ts", "var a = 1;\n")],
        "want": ["findings", "edits"],
        "limits": {"max_findings": 10, "max_capture_chars": 200, "max_preview_chars": 200},
    }));
    assert_eq!(response["error"]["type"], "unsupported_capability");
}

#[test]
fn nested_matches_keep_only_the_outermost_edit() {
    let source = "f(f(x));\n";
    let response = reply(&json!({
        "id": 91,
        "op": "structural",
        "language": "TypeScript",
        "matcher": {"kind": "pattern", "pattern": "f($A)"},
        "fix": {"text": "g($A)"},
        "transforms": null,
        "files": [file("nested.ts", source)],
        "want": ["findings", "edits"],
        "limits": {"max_findings": 10, "max_capture_chars": 200, "max_preview_chars": 200},
    }));
    // Findings keep both matches; edits drop the strictly nested inner one.
    assert_eq!(response["findings"].as_array().expect("findings").len(), 2);
    let edits = response["edits"].as_array().expect("edits");
    assert_eq!(edits.len(), 1);
    assert_eq!(edits[0]["byte_start"], 0);
    assert_eq!(edits[0]["byte_end"], 7);
    assert_eq!(edits[0]["inserted_text"], "g(f(x))");
    assert_eq!(apply_edit(source, &edits[0]), "g(f(x));\n");
}

#[test]
fn fix_expansion_extends_the_replaced_range() {
    let source = "a(); b(); c();\n";
    let response = reply(&json!({
        "id": 101,
        "op": "structural",
        "language": "TypeScript",
        "matcher": {"kind": "pattern", "pattern": "a();"},
        "fix": {"template": "x();", "expand_start": null, "expand_end": "b();"},
        "transforms": null,
        "files": [file("expand.ts", source)],
        "want": ["findings", "edits"],
        "limits": {"max_findings": 10, "max_capture_chars": 200, "max_preview_chars": 200},
    }));
    let edits = response["edits"].as_array().expect("edits");
    assert_eq!(edits.len(), 1, "{response}");
    assert_eq!(edits[0]["byte_start"], 0);
    assert_eq!(edits[0]["byte_end"], 9);
    assert_eq!(apply_edit(source, &edits[0]), "x(); c();\n");
}

#[test]
fn partially_overlapping_expanded_edits_are_rejected() {
    // Every statement matches; the fix expands each match through the next
    // `b();` sibling, so the first two expanded edits overlap partially.
    let response = reply(&json!({
        "id": 111,
        "op": "structural",
        "language": "TypeScript",
        "matcher": {"kind": "rule", "rule": {"kind": "expression_statement"}},
        "fix": {"template": "x();", "expand_start": null, "expand_end": "b();"},
        "transforms": null,
        "files": [file("overlap.ts", "a(); b(); c();\n")],
        "want": ["findings", "edits"],
        "limits": {"max_findings": 10, "max_capture_chars": 200, "max_preview_chars": 200},
    }));
    assert_eq!(response["id"], 111);
    assert_eq!(response["error"]["type"], "overlapping_edits", "{response}");
}

#[test]
fn per_file_content_failures_are_reported_not_fatal() {
    let response = reply(&json!({
        "id": 121,
        "op": "structural",
        "language": "TypeScript",
        "matcher": {"kind": "pattern", "pattern": "f($A)"},
        "fix": null,
        "transforms": null,
        "files": [
            {"path": "bad.ts", "content_b64": "!!!not-base64!!!"},
            file("good.ts", "f(1);\n"),
        ],
        "want": ["findings"],
        "limits": {"max_findings": 10, "max_capture_chars": 200, "max_preview_chars": 200},
    }));
    assert_eq!(response["findings"].as_array().expect("findings").len(), 1);
    let errors = response["errors"].as_array().expect("errors");
    assert_eq!(errors.len(), 1);
    assert_eq!(errors[0]["path"], "bad.ts");
}

#[test]
fn read_frame_rejects_oversized_lines_and_recovers() {
    let mut input = vec![b'a'; MAX_FRAME_BYTES + 1];
    input.push(b'\n');
    input.extend_from_slice(b"{\"id\":1,\"op\":\"ping\"}\n");
    let mut reader = std::io::Cursor::new(input);
    let Some(Frame::Oversized) = read_frame(&mut reader).expect("read") else {
        panic!("oversized line must be rejected");
    };
    let Some(Frame::Line(line)) = read_frame(&mut reader).expect("read") else {
        panic!("worker must recover after an oversized frame");
    };
    assert_eq!(line, b"{\"id\":1,\"op\":\"ping\"}");
    assert!(read_frame(&mut reader).expect("read").is_none());
}
