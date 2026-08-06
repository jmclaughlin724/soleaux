#!/usr/bin/env python3
"""Apply the review-driven fail-closed fixes for the P5 client matrix."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path.cwd().resolve()


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    (ROOT / path).write_text(content, encoding="utf-8")


def replace_once(content: str, old: str, new: str, label: str) -> str:
    count = content.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return content.replace(old, new, 1)


compatibility = read("native/daemon/ipc/src/compatibility.rs")
digest_owner = '''pub fn client_capability_matrix_sha256() -> String {
    let digest = Sha256::digest(CLIENT_CAPABILITY_MATRIX_JSON.as_bytes());
    format!("{digest:x}")
}
'''
canonical_helpers = digest_owner + r'''
fn canonical_probe_sha256(value: &Value) -> Result<String> {
    let mut basis = value.clone();
    let object = basis
        .as_object_mut()
        .context("probe evidence must be a JSON object")?;
    object.remove("evidenceSha256");
    let mut encoded = Vec::new();
    write_canonical_json(&basis, &mut encoded)?;
    let digest = Sha256::digest(&encoded);
    Ok(format!("{digest:x}"))
}

fn write_canonical_json(value: &Value, output: &mut Vec<u8>) -> Result<()> {
    match value {
        Value::Array(items) => {
            output.push(b'[');
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    output.push(b',');
                }
                write_canonical_json(item, output)?;
            }
            output.push(b']');
        }
        Value::Object(object) => {
            output.push(b'{');
            let mut keys = object.keys().collect::<Vec<_>>();
            keys.sort_unstable();
            for (index, key) in keys.into_iter().enumerate() {
                if index > 0 {
                    output.push(b',');
                }
                serde_json::to_writer(&mut *output, key)
                    .context("serializing a canonical probe key")?;
                output.push(b':');
                write_canonical_json(&object[key], output)?;
            }
            output.push(b'}');
        }
        _ => {
            serde_json::to_writer(&mut *output, value)
                .context("serializing a canonical probe value")?;
        }
    }
    Ok(())
}
'''
compatibility = replace_once(
    compatibility,
    digest_owner,
    canonical_helpers,
    "insert canonical probe hash owner",
)
valid_old = '''    let valid = probe.schema_version == CLIENT_CAPABILITY_PROBE_SCHEMA_VERSION
        && probe.platform == platform.id
        && probe.client_version == client_version
        && probe.matrix_sha256 == matrix_sha256
        && probe.status == "pass"
        && probe.mutation_eligible
        && is_lower_hex_digest(&probe.evidence_sha256)
        && missing.is_empty();
'''
valid_new = '''    let expected_evidence_sha256 = canonical_probe_sha256(probe_value)?;
    let valid = probe.schema_version == CLIENT_CAPABILITY_PROBE_SCHEMA_VERSION
        && probe.platform == platform.id
        && probe.client_version == client_version
        && probe.matrix_sha256 == matrix_sha256
        && probe.status == "pass"
        && probe.mutation_eligible
        && is_lower_hex_digest(&probe.evidence_sha256)
        && probe.evidence_sha256 == expected_evidence_sha256
        && missing.is_empty();
'''
compatibility = replace_once(
    compatibility,
    valid_old,
    valid_new,
    "bind probe admission to the recomputed hash",
)
helper_pattern = re.compile(
    r'''    fn valid_generic_probe\(\) -> \(Value, Value\) \{\n.*?\n    \}\n\n    #\[test\]''',
    re.DOTALL,
)
helper_replacement = r'''    fn with_evidence_sha256(mut probe: Value) -> Value {
        let digest = canonical_probe_sha256(&probe).expect("canonical probe digest");
        probe["evidenceSha256"] = Value::String(digest);
        probe
    }

    fn valid_generic_probe() -> (Value, Value) {
        let required = vec![
            "initialize",
            "tools_list",
            "context_compile",
            "registry_registration",
            "read_write_binding",
            "tool_ceiling",
        ];
        let probe = with_evidence_sha256(json!({
            "schemaVersion":CLIENT_CAPABILITY_PROBE_SCHEMA_VERSION,
            "platform":"generic_mcp_host",
            "clientVersion":"mcp-2025-11-25",
            "matrixSha256":client_capability_matrix_sha256(),
            "status":"pass",
            "mutationEligible":true,
            "passedSignals":required,
        }));
        (
            json!({"soleauxProbe":probe}),
            json!({"platform":"generic_mcp_host"}),
        )
    }

    #[test]'''
compatibility, count = helper_pattern.subn(helper_replacement, compatibility, count=1)
if count != 1:
    raise SystemExit(f"replace generic probe test helper: expected one match, found {count}")
compatibility = replace_once(
    compatibility,
    '        invalid["soleauxProbe"]["matrixSha256"] = Value::String("b".repeat(64));\n',
    '        invalid["soleauxProbe"]["evidenceSha256"] = Value::String("b".repeat(64));\n',
    "make the test exercise forged evidence hashes",
)
write("native/daemon/ipc/src/compatibility.rs", compatibility)

validator = read("native/scripts/validate_client_capability_matrix.py")
validator = replace_once(
    validator,
    '    "cursor": "runtime-observed",\n',
    '    "cursor": "supported-current",\n',
    "freeze the Cursor documentation-contract version",
)
validator = replace_once(
    validator,
    '''    elif "path" in source:
        path = ROOT / str(source["path"])
        if not path.is_file():
            fail(f"{platform} source path does not exist: {source['path']}")
''',
    '''    elif "path" in source:
        relative = Path(str(source["path"]))
        if relative.is_absolute():
            fail(f"{platform} source path must be repository relative: {source['path']}")
        path = (ROOT / relative).resolve()
        if path != ROOT and ROOT not in path.parents:
            fail(f"{platform} source path escapes the repository: {source['path']}")
        if not path.is_file():
            fail(f"{platform} source path does not exist: {source['path']}")
''',
    "jail evidence paths to the repository root",
)
write("native/scripts/validate_client_capability_matrix.py", validator)

probe = read("native/scripts/probe_client_capabilities.py")
probe = replace_once(
    probe,
    '    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")\n',
    '    encoded = json.dumps(\n        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False\n    ).encode("utf-8")\n',
    "align the Python canonical hash with the Rust owner",
)
probe = replace_once(
    probe,
    '''    if version_policy == "exact":
        if matrix_version not in combined:
            fail(
                "binary version did not match exact matrix entry "
                f"{matrix_version}: {combined[:500]}"
            )
        return matrix_version
''',
    '''    if version_policy == "exact":
        if observed != matrix_version:
            fail(
                "binary version did not match exact matrix entry "
                f"{matrix_version}; observed={observed or '<missing>'}: {combined[:500]}"
            )
        return matrix_version
''',
    "compare the parsed exact semantic version",
)
write("native/scripts/probe_client_capabilities.py", probe)

smoke = read("native/scripts/p5_client_matrix_smoke.py")
smoke = replace_once(
    smoke,
    '    encoded = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")\n',
    '    encoded = json.dumps(\n        value, separators=(",", ":"), sort_keys=True, ensure_ascii=False\n    ).encode("utf-8")\n',
    "align the smoke canonical hash with the Rust owner",
)
probe_pattern = re.compile(
    r'''    probe_basis = \{\n.*?\n    probe = \{\n.*?\n    \}\n''',
    re.DOTALL,
)
probe_replacement = '''    probe = {
        "schemaVersion": "soleaux.client-capability-probe/v1",
        "platform": "generic_mcp_host",
        "clientVersion": "mcp-2025-11-25",
        "matrixSha256": matrix_sha256,
        "status": "pass",
        "mutationEligible": True,
        "passedSignals": required_signals,
    }
    probe["evidenceSha256"] = canonical_sha256(probe)
'''
smoke, count = probe_pattern.subn(probe_replacement, smoke, count=1)
if count != 1:
    raise SystemExit(f"replace smoke probe hash basis: expected one match, found {count}")
write("native/scripts/p5_client_matrix_smoke.py", smoke)

matrix_path = ROOT / "native/contracts/client-capability-matrix-v1.json"
matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
cursor = next(item for item in matrix["platforms"] if item["id"] == "cursor")
cursor["probeMode"] = "documentation_contract"
cursor["versionPolicy"] = "supported_current"
cursor["capabilities"]["writePolicy"] = "read_only_documented_surface"
cursor_version = cursor["versions"][0]
cursor_version["version"] = "supported-current"
cursor_version["releaseChannel"] = "cursor-supported-current"
cursor_version["requiredBinarySignals"] = []
cursor_version["binaryCommands"] = {}
matrix_path.write_text(json.dumps(matrix, indent=2) + "\n", encoding="utf-8")

workflow = read(".github/workflows/client-capability-matrix.yml")
cursor_pattern = re.compile(r"\n  cursor:\n.*?\n  generic-mcp:\n", re.DOTALL)
cursor_job = r'''
  cursor:
    name: Cursor supported-surface contract
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - name: Validate the documented rules, MCP, hooks, and session surface
        run: |
          set -euo pipefail
          mkdir -p /tmp/p5-capability-cursor
          python3 native/scripts/validate_client_capability_matrix.py \
            --platform cursor \
            --output /tmp/p5-capability-cursor/matrix-validation.json
          python3 native/scripts/probe_client_capabilities.py \
            --platform cursor \
            --documentation-only \
            --output /tmp/p5-capability-cursor/probe.json
      - uses: actions/upload-artifact@v4
        with:
          name: p5-capability-cursor
          path: /tmp/p5-capability-cursor
          if-no-files-found: error
          retention-days: 30

  generic-mcp:
'''
workflow, count = cursor_pattern.subn(cursor_job, workflow, count=1)
if count != 1:
    raise SystemExit(f"replace Cursor workflow job: expected one match, found {count}")
write(".github/workflows/client-capability-matrix.yml", workflow)

docs = read("docs/testing/CLIENT-CAPABILITY-MATRIX.md")
docs = replace_once(
    docs,
    "| Cursor CLI / editor | runtime-observed current | P5-006 | Official installer, version/help binary probe, shared MCP configuration, rules, and hook documentation | Denied until an exact version is frozen and authenticated |\n",
    "| Cursor CLI / editor | supported current surface | P5-006 | Official rules, MCP, hooks, CLI, and session documentation; no moving installer is executed in evidence CI | Denied; documentation-contract surface only |\n",
    "update the Cursor safety table",
)
docs = replace_once(
    docs,
    "Cursor uses its official install channel, reports the observed CLI version, and is probed for version/help while its rules, MCP, and hooks surfaces are recorded from official documentation.  Since the installer tracks the current channel rather than a frozen artifact in this contract, Cursor remains read-only.\n",
    "Cursor is represented by a documentation-only supported-surface contract for rules, MCP configuration, hooks, CLI behavior, and native session history.  The evidence workflow does not execute Cursor's moving remote installer, and the entry remains read-only until a checksum-pinned version and authenticated lifecycle oracle are approved.\n",
    "replace the moving Cursor installer description",
)
docs = replace_once(
    docs,
    "The daemon independently recomputes the embedded matrix SHA-256 and refuses write access if any field or signal is absent or mismatched.\n",
    "The daemon independently recomputes both the embedded matrix SHA-256 and the canonical probe evidence SHA-256, then refuses write access if any field, digest, or required signal is absent or mismatched.\n",
    "document the canonical evidence-hash verification",
)
write("docs/testing/CLIENT-CAPABILITY-MATRIX.md", docs)

print("P5 client matrix review fixes applied")
