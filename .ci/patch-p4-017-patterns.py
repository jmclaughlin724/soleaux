#!/usr/bin/env python3
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "native/daemon/mcp/src/schema.rs"
EXPECTED_BASE_SHA256 = "411caa8e3d8f347d30a6b8d9d2ed1a06826b12a49be6d239671c416e2d97fb70"
EXPECTED_PATCHED_BYTES = 25556
EXPECTED_PATCHED_SHA256 = "6fb90909e3b5282d23c94d58702b7a510ce57d603c3f1cb8c340ef5ddb42db12"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


raw = PATH.read_bytes()
if hashlib.sha256(raw).hexdigest() != EXPECTED_BASE_SHA256:
    raise SystemExit("unexpected base schema source before pattern patch")
text = raw.decode("utf-8")
text = replace_once(
    text,
    "const ALLOWED_SCHEMA_KEYWORDS: [&str; 51] = [",
    "const ALLOWED_SCHEMA_KEYWORDS: [&str; 52] = [",
    "allowed keyword count",
)
text = replace_once(
    text,
    '    "minLength",\n    "maxItems",',
    '    "minLength",\n    "pattern",\n    "maxItems",',
    "pattern keyword",
)
text = replace_once(
    text,
    '''            for keyword in ["properties", "$defs", "definitions", "dependentSchemas"] {
                if let Some(properties) = object.get(keyword).and_then(Value::as_object) {
                    for (name, value) in properties {
                        ensure_supported_schema(
                            value,
                            &format!("{path}.{keyword}.{}", escape_path(name)),
                        )?;
                    }
                }
            }
            Ok(())
''',
    '''            for keyword in ["properties", "$defs", "definitions", "dependentSchemas"] {
                if let Some(properties) = object.get(keyword).and_then(Value::as_object) {
                    for (name, value) in properties {
                        ensure_supported_schema(
                            value,
                            &format!("{path}.{keyword}.{}", escape_path(name)),
                        )?;
                    }
                }
            }
            if let Some(pattern) = object.get("pattern") {
                let pattern = pattern
                    .as_str()
                    .with_context(|| format!("JSON Schema pattern at {path} must be a string"))?;
                ensure_supported_pattern(pattern)
                    .with_context(|| format!("unsupported JSON Schema pattern at {path}"))?;
            }
            Ok(())
''',
    "schema pattern support check",
)
text = replace_once(
    text,
    '''    if let Some(maximum) = schema.get("maxLength").and_then(Value::as_u64)
        && length > maximum as usize
    {
        push_violation(
            violations,
            path,
            format!("must contain at most {maximum} characters"),
        );
    }
}

fn validate_number(
''',
    '''    if let Some(maximum) = schema.get("maxLength").and_then(Value::as_u64)
        && length > maximum as usize
    {
        push_violation(
            violations,
            path,
            format!("must contain at most {maximum} characters"),
        );
    }
    if let Some(pattern) = schema.get("pattern").and_then(Value::as_str) {
        match matches_supported_pattern(pattern, value) {
            Ok(true) => {}
            Ok(false) => push_violation(
                violations,
                path,
                format!("must match the declared pattern `{pattern}`"),
            ),
            Err(error) => push_violation(violations, path, error.to_string()),
        }
    }
}

fn ensure_supported_pattern(pattern: &str) -> Result<()> {
    match pattern {
        "^(?!/)(?!.*(?:^|/)\\\\.\\\\.(?:/|$)).+$" | "^[0-9a-f]{64}$" => Ok(()),
        _ => bail!("unsupported JSON Schema pattern `{pattern}`"),
    }
}

fn matches_supported_pattern(pattern: &str, value: &str) -> Result<bool> {
    ensure_supported_pattern(pattern)?;
    match pattern {
        "^(?!/)(?!.*(?:^|/)\\\\.\\\\.(?:/|$)).+$" => Ok(
            !value.is_empty()
                && !value.starts_with('/')
                && !value.split('/').any(|segment| segment == ".."),
        ),
        "^[0-9a-f]{64}$" => Ok(
            value.len() == 64
                && value
                    .bytes()
                    .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)),
        ),
        _ => unreachable!("pattern support was checked above"),
    }
}

fn validate_number(
''',
    "locked pattern assertions",
)
text = replace_once(
    text,
    '''    fn unsupported_assertions_fail_closed() {
        let schema = json!({"type":"string","pattern":"^[a-z]+$"});
        let error = validate_json_schema(&schema, &json!("valid"))
            .expect_err("pattern is unsupported")
            .to_string();
        assert!(error.contains("unsupported JSON Schema keyword `pattern`"));
    }
''',
    '''    fn unsupported_assertions_fail_closed() {
        let schema = json!({"type":"object","unevaluatedProperties":false});
        let error = validate_json_schema(&schema, &json!({}))
            .expect_err("unevaluatedProperties is unsupported")
            .to_string();
        assert!(error.contains("unsupported JSON Schema keyword `unevaluatedProperties`"));
    }

    #[test]
    fn locked_patterns_are_enforced() {
        let relative_path = json!({
            "type":"string",
            "pattern":"^(?!/)(?!.*(?:^|/)\\\\.\\\\.(?:/|$)).+$"
        });
        assert!(validate_json_schema(&relative_path, &json!("src/lib.rs")).is_ok());
        assert!(validate_json_schema(&relative_path, &json!("/etc/passwd")).is_err());
        assert!(validate_json_schema(&relative_path, &json!("src/../secret")).is_err());

        let digest = json!({"type":"string","pattern":"^[0-9a-f]{64}$"});
        assert!(validate_json_schema(&digest, &json!("a".repeat(64))).is_ok());
        assert!(validate_json_schema(&digest, &json!("A".repeat(64))).is_err());
        assert!(validate_json_schema(&digest, &json!("a".repeat(63))).is_err());
    }
''',
    "pattern regression tests",
)
patched = text.encode("utf-8")
if len(patched) != EXPECTED_PATCHED_BYTES:
    raise SystemExit(f"unexpected patched schema size: {len(patched)}")
if hashlib.sha256(patched).hexdigest() != EXPECTED_PATCHED_SHA256:
    raise SystemExit("patched schema digest mismatch")
PATH.write_bytes(patched)
