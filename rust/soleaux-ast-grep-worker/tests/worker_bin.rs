//! End-to-end test of the built binary over real stdin/stdout JSONL frames.

use std::io::{Read, Write};
use std::process::{Command, Stdio};

use base64::Engine as _;
use base64::engine::general_purpose::STANDARD as BASE64;
use serde_json::{Value, json};

#[test]
fn binary_serves_jsonl_and_exits_on_shutdown() {
    let mut child = Command::new(env!("CARGO_BIN_EXE_soleaux-ast-grep-worker"))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("worker binary must start");

    let structural = json!({
        "id": 2,
        "op": "structural",
        "language": "TypeScript",
        "matcher": {"kind": "pattern", "pattern": "var $A = $B"},
        "fix": {"text": "let $A = $B"},
        "transforms": null,
        "files": [{"path": "smoke.ts", "content_b64": BASE64.encode("var a = 1;\n")}],
        "want": ["findings", "edits"],
        "limits": {"max_findings": 5, "max_capture_chars": 200, "max_preview_chars": 200},
    });
    {
        let stdin = child.stdin.as_mut().expect("stdin");
        stdin
            .write_all(b"{\"id\": 1, \"op\": \"ping\"}\n")
            .expect("write ping");
        stdin
            .write_all(format!("{structural}\n").as_bytes())
            .expect("write structural");
        stdin
            .write_all(b"this is not json\n")
            .expect("write garbage");
        let mut oversized = vec![b'x'; 8 * 1024 * 1024 + 1];
        oversized.push(b'\n');
        stdin.write_all(&oversized).expect("write oversized frame");
        stdin
            .write_all(b"{\"id\": 4, \"op\": \"shutdown\"}\n")
            .expect("write shutdown");
    }
    drop(child.stdin.take());

    let mut stdout = String::new();
    child
        .stdout
        .take()
        .expect("stdout")
        .read_to_string(&mut stdout)
        .expect("read stdout");
    let status = child.wait().expect("wait");
    assert!(status.success(), "worker must exit 0 on shutdown");

    let replies: Vec<Value> = stdout
        .lines()
        .map(|line| serde_json::from_str(line).expect("stdout must carry only JSON lines"))
        .collect();
    assert_eq!(replies.len(), 5, "stdout: {stdout}");
    assert_eq!(
        replies[0],
        json!({
            "id": 1,
            "ok": true,
            "engine": "rust",
            "engine_version": "0.45.0",
            "capabilities": ["soleaux.structural/v1"],
        })
    );
    assert_eq!(replies[1]["id"], 2);
    assert_eq!(replies[1]["engine"], "rust");
    assert_eq!(
        replies[1]["findings"].as_array().expect("findings").len(),
        1
    );
    assert_eq!(replies[1]["edits"][0]["inserted_text"], "let a = 1");
    assert_eq!(replies[2]["error"]["type"], "protocol");
    assert!(replies[2]["id"].is_null());
    assert_eq!(replies[3]["error"]["type"], "protocol");
    assert_eq!(replies[4], json!({"id": 4, "ok": true}));
}
