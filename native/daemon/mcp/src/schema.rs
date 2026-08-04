//! Minimal fail-closed JSON Schema validation for locked MCP input schemas.
//!
//! The public profile embeds self-contained Draft 2020-12 input schemas. This
//! validator intentionally implements the assertion keywords used by those
//! schemas without adding an unpinned runtime dependency. Unsupported
//! assertion keywords fail closed instead of being silently ignored.

use anyhow::{Context, Result, bail};
use serde_json::Value;

const MAX_VIOLATIONS: usize = 16;
const ALLOWED_SCHEMA_KEYWORDS: [&str; 52] = [
    "$schema",
    "$id",
    "$anchor",
    "$comment",
    "$defs",
    "definitions",
    "$ref",
    "title",
    "description",
    "default",
    "examples",
    "deprecated",
    "readOnly",
    "writeOnly",
    "format",
    "contentEncoding",
    "contentMediaType",
    "type",
    "enum",
    "const",
    "multipleOf",
    "maximum",
    "exclusiveMaximum",
    "minimum",
    "exclusiveMinimum",
    "maxLength",
    "minLength",
    "pattern",
    "maxItems",
    "minItems",
    "uniqueItems",
    "contains",
    "maxContains",
    "minContains",
    "maxProperties",
    "minProperties",
    "required",
    "dependentRequired",
    "properties",
    "additionalProperties",
    "propertyNames",
    "prefixItems",
    "items",
    "additionalItems",
    "allOf",
    "anyOf",
    "oneOf",
    "not",
    "if",
    "then",
    "else",
    "dependentSchemas",
];

pub(crate) fn validate_json_schema(schema: &Value, instance: &Value) -> Result<()> {
    validate_schema_definition(schema)?;
    let mut violations = Vec::new();
    validate_at(schema, schema, instance, "$", &mut violations);
    if violations.is_empty() {
        return Ok(());
    }
    bail!("{}", violations.join("; "))
}

pub(crate) fn validate_schema_definition(schema: &Value) -> Result<()> {
    ensure_supported_schema(schema, "$")
}

fn ensure_supported_schema(schema: &Value, path: &str) -> Result<()> {
    match schema {
        Value::Bool(_) => Ok(()),
        Value::Object(object) => {
            for keyword in object.keys() {
                if !ALLOWED_SCHEMA_KEYWORDS.contains(&keyword.as_str()) {
                    bail!("unsupported JSON Schema keyword `{keyword}` at {path}");
                }
            }
            for keyword in ["allOf", "anyOf", "oneOf", "prefixItems"] {
                if let Some(items) = object.get(keyword).and_then(Value::as_array) {
                    for (index, item) in items.iter().enumerate() {
                        ensure_supported_schema(item, &format!("{path}.{keyword}[{index}]"))?;
                    }
                }
            }
            for keyword in [
                "not",
                "if",
                "then",
                "else",
                "items",
                "contains",
                "additionalProperties",
                "additionalItems",
                "propertyNames",
            ] {
                if let Some(value) = object.get(keyword)
                    && (value.is_object() || value.is_boolean())
                {
                    ensure_supported_schema(value, &format!("{path}.{keyword}"))?;
                }
            }
            for keyword in ["properties", "$defs", "definitions", "dependentSchemas"] {
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
        }
        _ => bail!("JSON Schema at {path} must be an object or boolean"),
    }
}

fn validate_at(
    root: &Value,
    schema: &Value,
    instance: &Value,
    path: &str,
    violations: &mut Vec<String>,
) {
    if violations.len() >= MAX_VIOLATIONS {
        return;
    }
    if let Some(allowed) = schema.as_bool() {
        if !allowed {
            push_violation(violations, path, "is rejected by the schema");
        }
        return;
    }
    let Some(object) = schema.as_object() else {
        push_violation(violations, path, "has an invalid schema definition");
        return;
    };

    if let Some(reference) = object.get("$ref").and_then(Value::as_str) {
        match resolve_reference(root, reference) {
            Ok(target) => validate_at(root, target, instance, path, violations),
            Err(error) => push_violation(violations, path, error.to_string()),
        }
    }

    validate_combinators(root, object, instance, path, violations);

    if let Some(expected) = object.get("const")
        && instance != expected
    {
        push_violation(
            violations,
            path,
            format!("must equal the declared constant {expected}"),
        );
    }
    if let Some(allowed) = object.get("enum").and_then(Value::as_array)
        && !allowed.iter().any(|candidate| candidate == instance)
    {
        push_violation(
            violations,
            path,
            format!("must be one of {}", Value::Array(allowed.clone())),
        );
    }

    if let Some(expected_type) = object.get("type")
        && !matches_declared_type(instance, expected_type)
    {
        push_violation(
            violations,
            path,
            format!(
                "must have type {}, received {}",
                type_description(expected_type),
                value_type_name(instance)
            ),
        );
        return;
    }

    if let Some(value) = instance.as_object() {
        validate_object(root, object, value, path, violations);
    }
    if let Some(value) = instance.as_array() {
        validate_array(root, object, value, path, violations);
    }
    if let Some(value) = instance.as_str() {
        validate_string(object, value, path, violations);
    }
    if instance.is_number() {
        validate_number(object, instance, path, violations);
    }
}

fn validate_combinators(
    root: &Value,
    schema: &serde_json::Map<String, Value>,
    instance: &Value,
    path: &str,
    violations: &mut Vec<String>,
) {
    if let Some(all_of) = schema.get("allOf").and_then(Value::as_array) {
        for branch in all_of {
            validate_at(root, branch, instance, path, violations);
        }
    }
    if let Some(any_of) = schema.get("anyOf").and_then(Value::as_array) {
        let matches = any_of
            .iter()
            .filter(|branch| schema_matches(root, branch, instance, path))
            .count();
        if matches == 0 {
            push_violation(violations, path, "must match at least one `anyOf` branch");
        }
    }
    if let Some(one_of) = schema.get("oneOf").and_then(Value::as_array) {
        let matches = one_of
            .iter()
            .filter(|branch| schema_matches(root, branch, instance, path))
            .count();
        if matches != 1 {
            push_violation(
                violations,
                path,
                format!("must match exactly one `oneOf` branch; matched {matches}"),
            );
        }
    }
    if let Some(not_schema) = schema.get("not")
        && schema_matches(root, not_schema, instance, path)
    {
        push_violation(violations, path, "matches a prohibited `not` schema");
    }
    if let Some(if_schema) = schema.get("if") {
        let branch = if schema_matches(root, if_schema, instance, path) {
            schema.get("then")
        } else {
            schema.get("else")
        };
        if let Some(branch) = branch {
            validate_at(root, branch, instance, path, violations);
        }
    }
}

fn validate_object(
    root: &Value,
    schema: &serde_json::Map<String, Value>,
    object: &serde_json::Map<String, Value>,
    path: &str,
    violations: &mut Vec<String>,
) {
    if let Some(minimum) = schema.get("minProperties").and_then(Value::as_u64)
        && object.len() < minimum as usize
    {
        push_violation(
            violations,
            path,
            format!("must contain at least {minimum} properties"),
        );
    }
    if let Some(maximum) = schema.get("maxProperties").and_then(Value::as_u64)
        && object.len() > maximum as usize
    {
        push_violation(
            violations,
            path,
            format!("must contain at most {maximum} properties"),
        );
    }

    if let Some(required) = schema.get("required").and_then(Value::as_array) {
        for name in required.iter().filter_map(Value::as_str) {
            if !object.contains_key(name) {
                push_violation(
                    violations,
                    &property_path(path, name),
                    "is a required property",
                );
            }
        }
    }

    let properties = schema.get("properties").and_then(Value::as_object);
    for (name, value) in object {
        let child_path = property_path(path, name);
        if let Some(property_schema) = properties.and_then(|items| items.get(name)) {
            validate_at(root, property_schema, value, &child_path, violations);
            continue;
        }
        match schema.get("additionalProperties") {
            Some(Value::Bool(false)) => {
                push_violation(violations, &child_path, "is not an allowed property");
            }
            Some(additional) if additional.is_object() || additional.is_boolean() => {
                validate_at(root, additional, value, &child_path, violations);
            }
            _ => {}
        }
    }

    if let Some(property_names) = schema.get("propertyNames") {
        for name in object.keys() {
            validate_at(
                root,
                property_names,
                &Value::String(name.clone()),
                &format!("{path}{{propertyName:{}}}", escape_path(name)),
                violations,
            );
        }
    }

    if let Some(dependencies) = schema.get("dependentRequired").and_then(Value::as_object) {
        for (trigger, required) in dependencies {
            if !object.contains_key(trigger) {
                continue;
            }
            for name in required
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
            {
                if !object.contains_key(name) {
                    push_violation(
                        violations,
                        &property_path(path, name),
                        format!("is required when `{trigger}` is present"),
                    );
                }
            }
        }
    }

    if let Some(dependencies) = schema.get("dependentSchemas").and_then(Value::as_object) {
        for (trigger, dependent_schema) in dependencies {
            if object.contains_key(trigger) {
                validate_at(
                    root,
                    dependent_schema,
                    &Value::Object(object.clone()),
                    path,
                    violations,
                );
            }
        }
    }
}

fn validate_array(
    root: &Value,
    schema: &serde_json::Map<String, Value>,
    array: &[Value],
    path: &str,
    violations: &mut Vec<String>,
) {
    if let Some(minimum) = schema.get("minItems").and_then(Value::as_u64)
        && array.len() < minimum as usize
    {
        push_violation(
            violations,
            path,
            format!("must contain at least {minimum} items"),
        );
    }
    if let Some(maximum) = schema.get("maxItems").and_then(Value::as_u64)
        && array.len() > maximum as usize
    {
        push_violation(
            violations,
            path,
            format!("must contain at most {maximum} items"),
        );
    }
    if schema.get("uniqueItems").and_then(Value::as_bool) == Some(true) {
        for left in 0..array.len() {
            if array[left + 1..].contains(&array[left]) {
                push_violation(violations, path, "must contain unique items");
                break;
            }
        }
    }

    let prefix_items = schema
        .get("prefixItems")
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .unwrap_or_default();
    for (index, item_schema) in prefix_items.iter().enumerate() {
        if let Some(item) = array.get(index) {
            validate_at(
                root,
                item_schema,
                item,
                &format!("{path}[{index}]"),
                violations,
            );
        }
    }

    if array.len() > prefix_items.len() {
        match schema.get("items") {
            Some(Value::Bool(false)) => push_violation(
                violations,
                path,
                format!(
                    "must not contain items after index {}",
                    prefix_items.len().saturating_sub(1)
                ),
            ),
            Some(item_schema) if item_schema.is_object() || item_schema.is_boolean() => {
                for (index, item) in array.iter().enumerate().skip(prefix_items.len()) {
                    validate_at(
                        root,
                        item_schema,
                        item,
                        &format!("{path}[{index}]"),
                        violations,
                    );
                }
            }
            _ if prefix_items.is_empty() => {
                if let Some(item_schema) = schema.get("additionalItems")
                    && (item_schema.is_object() || item_schema.is_boolean())
                {
                    for (index, item) in array.iter().enumerate() {
                        validate_at(
                            root,
                            item_schema,
                            item,
                            &format!("{path}[{index}]"),
                            violations,
                        );
                    }
                }
            }
            _ => {}
        }
    }

    if let Some(contains) = schema.get("contains") {
        let matching = array
            .iter()
            .enumerate()
            .filter(|(index, item)| {
                schema_matches(root, contains, item, &format!("{path}[{index}]"))
            })
            .count();
        let minimum = schema
            .get("minContains")
            .and_then(Value::as_u64)
            .unwrap_or(1) as usize;
        let maximum = schema
            .get("maxContains")
            .and_then(Value::as_u64)
            .map(|value| value as usize);
        if matching < minimum {
            push_violation(
                violations,
                path,
                format!("must contain at least {minimum} matching items"),
            );
        }
        if let Some(maximum) = maximum
            && matching > maximum
        {
            push_violation(
                violations,
                path,
                format!("must contain at most {maximum} matching items"),
            );
        }
    }
}

fn validate_string(
    schema: &serde_json::Map<String, Value>,
    value: &str,
    path: &str,
    violations: &mut Vec<String>,
) {
    let length = value.chars().count();
    if let Some(minimum) = schema.get("minLength").and_then(Value::as_u64)
        && length < minimum as usize
    {
        push_violation(
            violations,
            path,
            format!("must contain at least {minimum} characters"),
        );
    }
    if let Some(maximum) = schema.get("maxLength").and_then(Value::as_u64)
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
        "^(?!/)(?!.*(?:^|/)\\.\\.(?:/|$)).+$" | "^[0-9a-f]{64}$" => Ok(()),
        _ => bail!("unsupported JSON Schema pattern `{pattern}`"),
    }
}

fn matches_supported_pattern(pattern: &str, value: &str) -> Result<bool> {
    ensure_supported_pattern(pattern)?;
    match pattern {
        "^(?!/)(?!.*(?:^|/)\\.\\.(?:/|$)).+$" => Ok(!value.is_empty()
            && !value.starts_with('/')
            && !value.split('/').any(|segment| segment == "..")),
        "^[0-9a-f]{64}$" => Ok(value.len() == 64
            && value
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))),
        _ => unreachable!("pattern support was checked above"),
    }
}

fn validate_number(
    schema: &serde_json::Map<String, Value>,
    value: &Value,
    path: &str,
    violations: &mut Vec<String>,
) {
    let Some(number) = value.as_f64() else {
        return;
    };
    if let Some(minimum) = schema.get("minimum").and_then(Value::as_f64)
        && number < minimum
    {
        push_violation(
            violations,
            path,
            format!("must be greater than or equal to {minimum}"),
        );
    }
    if let Some(maximum) = schema.get("maximum").and_then(Value::as_f64)
        && number > maximum
    {
        push_violation(
            violations,
            path,
            format!("must be less than or equal to {maximum}"),
        );
    }
    if let Some(minimum) = schema.get("exclusiveMinimum").and_then(Value::as_f64)
        && number <= minimum
    {
        push_violation(violations, path, format!("must be greater than {minimum}"));
    }
    if let Some(maximum) = schema.get("exclusiveMaximum").and_then(Value::as_f64)
        && number >= maximum
    {
        push_violation(violations, path, format!("must be less than {maximum}"));
    }
    if let Some(divisor) = schema.get("multipleOf").and_then(Value::as_f64)
        && divisor > 0.0
    {
        let quotient = number / divisor;
        if (quotient - quotient.round()).abs() > f64::EPSILON * 8.0 {
            push_violation(violations, path, format!("must be a multiple of {divisor}"));
        }
    }
}

fn schema_matches(root: &Value, schema: &Value, instance: &Value, path: &str) -> bool {
    let mut violations = Vec::new();
    validate_at(root, schema, instance, path, &mut violations);
    violations.is_empty()
}

fn resolve_reference<'a>(root: &'a Value, reference: &str) -> Result<&'a Value> {
    let pointer = reference
        .strip_prefix('#')
        .context("only local JSON Schema references are supported")?;
    root.pointer(pointer)
        .with_context(|| format!("unresolved JSON Schema reference `{reference}`"))
}

fn matches_declared_type(instance: &Value, expected: &Value) -> bool {
    match expected {
        Value::String(name) => matches_type(instance, name),
        Value::Array(names) => names
            .iter()
            .filter_map(Value::as_str)
            .any(|name| matches_type(instance, name)),
        _ => false,
    }
}

fn matches_type(instance: &Value, expected: &str) -> bool {
    match expected {
        "null" => instance.is_null(),
        "boolean" => instance.is_boolean(),
        "object" => instance.is_object(),
        "array" => instance.is_array(),
        "number" => instance.is_number(),
        "integer" => instance.as_number().is_some_and(|number| {
            number.as_i64().is_some()
                || number.as_u64().is_some()
                || number
                    .as_f64()
                    .is_some_and(|value| value.is_finite() && value.fract() == 0.0)
        }),
        "string" => instance.is_string(),
        _ => false,
    }
}

fn type_description(expected: &Value) -> String {
    match expected {
        Value::String(name) => format!("`{name}`"),
        Value::Array(names) => names
            .iter()
            .filter_map(Value::as_str)
            .map(|name| format!("`{name}`"))
            .collect::<Vec<_>>()
            .join(" or "),
        _ => "a valid declared type".to_string(),
    }
}

fn value_type_name(value: &Value) -> &'static str {
    match value {
        Value::Null => "null",
        Value::Bool(_) => "boolean",
        Value::Number(number) if number.as_i64().is_some() || number.as_u64().is_some() => {
            "integer"
        }
        Value::Number(_) => "number",
        Value::String(_) => "string",
        Value::Array(_) => "array",
        Value::Object(_) => "object",
    }
}

fn property_path(path: &str, property: &str) -> String {
    if property
        .chars()
        .all(|character| character.is_ascii_alphanumeric() || character == '_')
    {
        format!("{path}.{property}")
    } else {
        format!(
            "{path}[{}]",
            serde_json::to_string(property).unwrap_or_default()
        )
    }
}

fn escape_path(value: &str) -> String {
    value.replace('~', "~0").replace('/', "~1")
}

fn push_violation(violations: &mut Vec<String>, path: &str, message: impl Into<String>) {
    if violations.len() < MAX_VIOLATIONS {
        violations.push(format!("{path} {}", message.into()));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn validates_required_unknown_types_and_ranges() {
        let schema = json!({
            "type":"object",
            "additionalProperties":false,
            "properties":{
                "name":{"type":"string","minLength":1},
                "limit":{"type":"integer","minimum":1,"maximum":10},
                "tags":{"type":"array","items":{"type":"string"},"uniqueItems":true}
            },
            "required":["name"]
        });
        assert!(
            validate_json_schema(&schema, &json!({"name":"ok","limit":10,"tags":["a","b"]}))
                .is_ok()
        );
        let error =
            validate_json_schema(&schema, &json!({"limit":0,"tags":["a","a"],"unknown":true}))
                .expect_err("invalid instance")
                .to_string();
        assert!(error.contains("$.name is a required property"));
        assert!(error.contains("$.limit must be greater than or equal to 1"));
        assert!(error.contains("$.tags must contain unique items"));
        assert!(error.contains("$.unknown is not an allowed property"));
    }

    #[test]
    fn validates_combinators_and_nullable_types() {
        let schema = json!({
            "type":"object",
            "additionalProperties":false,
            "properties":{
                "value":{"type":["string","null"]},
                "choice":{"oneOf":[{"const":"a"},{"const":"b"}]}
            },
            "required":["choice"]
        });
        assert!(validate_json_schema(&schema, &json!({"value":null,"choice":"a"})).is_ok());
        assert!(validate_json_schema(&schema, &json!({"value":1,"choice":"c"})).is_err());
    }

    #[test]
    fn unsupported_assertions_fail_closed() {
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
            "pattern":"^(?!/)(?!.*(?:^|/)\\.\\.(?:/|$)).+$"
        });
        assert!(validate_json_schema(&relative_path, &json!("src/lib.rs")).is_ok());
        assert!(validate_json_schema(&relative_path, &json!("/etc/passwd")).is_err());
        assert!(validate_json_schema(&relative_path, &json!("src/../secret")).is_err());

        let digest = json!({"type":"string","pattern":"^[0-9a-f]{64}$"});
        assert!(validate_json_schema(&digest, &json!("a".repeat(64))).is_ok());
        assert!(validate_json_schema(&digest, &json!("A".repeat(64))).is_err());
        assert!(validate_json_schema(&digest, &json!("a".repeat(63))).is_err());
    }

    #[test]
    fn local_references_and_integral_json_numbers_are_supported() {
        let schema = json!({
            "$defs":{"positive":{"type":"integer","minimum":1}},
            "type":"object",
            "additionalProperties":false,
            "properties":{"count":{"$ref":"#/$defs/positive"}},
            "required":["count"]
        });
        assert!(validate_json_schema(&schema, &json!({"count":1.0})).is_ok());
        assert!(validate_json_schema(&schema, &json!({"count":0})).is_err());
    }
}
