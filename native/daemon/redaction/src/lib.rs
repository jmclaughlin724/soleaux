//! Shared, fail-closed secret redaction for every Soleaux exposure boundary.
//!
//! The same implementation is used by repository context, Context Packet V2,
//! MCP envelopes and errors, telemetry, and the data structures later consumed
//! by memory, handoff, artifact, desktop, and mobile surfaces. The redactor is
//! deliberately deterministic and dependency-light so it can run before any
//! value leaves the trusted local process.

use serde_json::Value;

pub const REDACTED: &str = "[REDACTED]";
pub const REDACTED_PRIVATE_KEY: &str = "[REDACTED PRIVATE KEY]";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Redaction<T> {
    pub value: T,
    pub count: usize,
}

impl<T> Redaction<T> {
    fn new(value: T, count: usize) -> Self {
        Self { value, count }
    }
}

/// Redact secrets embedded in arbitrary text while preserving non-sensitive
/// structure and line endings.
pub fn redact_text(source: &str) -> Redaction<String> {
    let mut output = String::with_capacity(source.len());
    let mut count = 0usize;
    let mut in_private_key = false;

    for line in source.split_inclusive('\n') {
        let newline = line.ends_with('\n');
        let body = line.strip_suffix('\n').unwrap_or(line);
        let trimmed = body.trim();

        if in_private_key {
            count = count.saturating_add(1);
            output.push_str(REDACTED_PRIVATE_KEY);
            if newline {
                output.push('\n');
            }
            if is_private_key_end(trimmed) {
                in_private_key = false;
            }
            continue;
        }
        if is_private_key_begin(trimmed) {
            in_private_key = true;
            count = count.saturating_add(1);
            output.push_str(REDACTED_PRIVATE_KEY);
            if newline {
                output.push('\n');
            }
            continue;
        }

        let mut redacted = redact_sensitive_assignment(body);
        let prefixed = redact_prefixed_tokens(&redacted.value);
        redacted.value = prefixed.value;
        redacted.count = redacted.count.saturating_add(prefixed.count);
        let auth = redact_auth_schemes(&redacted.value);
        redacted.value = auth.value;
        redacted.count = redacted.count.saturating_add(auth.count);
        let urls = redact_url_credentials(&redacted.value);
        redacted.value = urls.value;
        redacted.count = redacted.count.saturating_add(urls.count);
        let jwt = redact_jwts(&redacted.value);
        redacted.value = jwt.value;
        redacted.count = redacted.count.saturating_add(jwt.count);

        count = count.saturating_add(redacted.count);
        output.push_str(&redacted.value);
        if newline {
            output.push('\n');
        }
    }

    Redaction::new(output, count)
}

/// Recursively redact sensitive JSON object keys and secret-looking strings.
pub fn redact_json_value(mut value: Value) -> Redaction<Value> {
    let count = redact_json_in_place(&mut value);
    Redaction::new(value, count)
}

/// Recursively redact a JSON value in place. Returns the number of redactions.
pub fn redact_json_in_place(value: &mut Value) -> usize {
    match value {
        Value::Object(object) => {
            let mut count = 0usize;
            for (key, child) in object {
                if is_sensitive_key(key) && should_replace_structured_value(child) {
                    *child = Value::String(REDACTED.to_string());
                    count = count.saturating_add(1);
                } else {
                    count = count.saturating_add(redact_json_in_place(child));
                }
            }
            count
        }
        Value::Array(items) => items.iter_mut().fold(0usize, |count, item| {
            count.saturating_add(redact_json_in_place(item))
        }),
        Value::String(text) => {
            let redacted = redact_text(text);
            *text = redacted.value;
            redacted.count
        }
        Value::Null | Value::Bool(_) | Value::Number(_) => 0,
    }
}

/// Return whether an object/header/environment key represents secret material.
/// Metric fields such as `input_tokens`, `token_budget`, and `max_tokens` are
/// intentionally not classified as credentials.
pub fn is_sensitive_key(key: &str) -> bool {
    let compact = key
        .chars()
        .filter(|character| character.is_ascii_alphanumeric())
        .flat_map(char::to_lowercase)
        .collect::<String>();

    if is_non_secret_token_metric(&compact) {
        return false;
    }

    matches!(
        compact.as_str(),
        "secret"
            | "token"
            | "password"
            | "passwd"
            | "passphrase"
            | "apikey"
            | "privatekey"
            | "privatekeyid"
            | "authorization"
            | "proxyauthorization"
            | "credential"
            | "credentials"
            | "cookie"
            | "setcookie"
            | "xapikey"
            | "xauthtoken"
            | "clientsecret"
            | "accesstoken"
            | "refreshtoken"
            | "authtoken"
            | "webhooksecret"
            | "signingsecret"
            | "encryptionkey"
            | "awssecretaccesskey"
            | "databasepassword"
    ) || compact.ends_with("secret")
        || compact.ends_with("password")
        || compact.ends_with("privatekey")
        || compact.ends_with("credential")
        || compact.ends_with("token")
}

fn should_replace_structured_value(value: &Value) -> bool {
    !matches!(value, Value::Null | Value::Bool(_) | Value::Number(_))
}

fn is_non_secret_token_metric(key: &str) -> bool {
    key.ends_with("tokens")
        || key.contains("tokenbudget")
        || key.contains("tokencount")
        || key.contains("tokenlimit")
        || key.contains("tokenusage")
        || key.contains("tokenwindow")
        || key.contains("contextwindow")
        || key == "maxtoken"
        || key == "mintoken"
        || key == "maxtokens"
        || key == "mintokens"
}

fn is_private_key_begin(value: &str) -> bool {
    value.starts_with("-----BEGIN ") && value.ends_with("PRIVATE KEY-----")
}

fn is_private_key_end(value: &str) -> bool {
    value.starts_with("-----END ") && value.ends_with("PRIVATE KEY-----")
}

fn redact_sensitive_assignment(line: &str) -> Redaction<String> {
    let Some((delimiter_index, delimiter)) = assignment_delimiter(line) else {
        return Redaction::new(line.to_string(), 0);
    };
    let left = &line[..delimiter_index];
    let key = trailing_identifier(left);
    if !is_sensitive_key(&key) {
        return Redaction::new(line.to_string(), 0);
    }
    let right = line[delimiter_index + delimiter.len_utf8()..].trim();
    if right.is_empty() || is_non_secret_literal(right) {
        return Redaction::new(line.to_string(), 0);
    }

    let mut rendered = String::with_capacity(delimiter_index + REDACTED.len() + 4);
    rendered.push_str(&line[..delimiter_index + delimiter.len_utf8()]);
    rendered.push(' ');
    rendered.push_str(REDACTED);
    if right.ends_with(',') {
        rendered.push(',');
    } else if right.ends_with(';') {
        rendered.push(';');
    }
    Redaction::new(rendered, 1)
}

fn assignment_delimiter(line: &str) -> Option<(usize, char)> {
    let bytes = line.as_bytes();
    for (index, byte) in bytes.iter().enumerate() {
        match *byte {
            b'=' => {
                let previous = index.checked_sub(1).and_then(|value| bytes.get(value));
                let next = bytes.get(index + 1);
                if matches!(previous, Some(b'=' | b'!' | b'<' | b'>')) || next == Some(&b'=') {
                    continue;
                }
                return Some((index, '='));
            }
            b':' => {
                let previous = index.checked_sub(1).and_then(|value| bytes.get(value));
                let next = bytes.get(index + 1);
                if previous == Some(&b':') || next == Some(&b':') || next == Some(&b'/') {
                    continue;
                }
                return Some((index, ':'));
            }
            _ => {}
        }
    }
    None
}

fn trailing_identifier(value: &str) -> String {
    let trimmed = value.trim_end();
    let end = trimmed.len();
    let start = trimmed
        .char_indices()
        .rev()
        .find_map(|(index, character)| {
            (!character.is_alphanumeric() && !matches!(character, '_' | '-'))
                .then_some(index + character.len_utf8())
        })
        .unwrap_or(0);
    trimmed[start..end]
        .trim_matches(|character| matches!(character, '\'' | '"'))
        .to_string()
}

fn is_non_secret_literal(value: &str) -> bool {
    let value = value
        .trim_end_matches([',', ';'])
        .trim_matches(|character| matches!(character, '\'' | '"'))
        .trim();
    value.is_empty()
        || matches!(value, "null" | "undefined" | "true" | "false")
        || value.parse::<f64>().is_ok()
}

fn redact_prefixed_tokens(line: &str) -> Redaction<String> {
    const PREFIXES: &[(&str, usize)] = &[
        ("github_pat_", 24),
        ("ghp_", 20),
        ("gho_", 20),
        ("ghu_", 20),
        ("ghs_", 20),
        ("ghr_", 20),
        ("glpat-", 20),
        ("xoxb-", 20),
        ("xoxp-", 20),
        ("xoxa-", 20),
        ("xoxr-", 20),
        ("sk-proj-", 24),
        ("sk_live_", 20),
        ("rk_live_", 20),
        ("whsec_", 20),
        ("sk-", 20),
        ("pypi-", 20),
        ("npm_", 20),
        ("hf_", 20),
        ("AKIA", 20),
        ("ASIA", 20),
        ("AIza", 24),
        ("ya29.", 20),
        ("SG.", 20),
    ];

    let mut output = line.to_string();
    let mut count = 0usize;
    for (prefix, minimum_length) in PREFIXES {
        let mut cursor = 0usize;
        while let Some(relative) = output[cursor..].find(prefix) {
            let start = cursor + relative;
            let mut end = start + prefix.len();
            while let Some(byte) = output.as_bytes().get(end) {
                if is_secret_token_byte(*byte) {
                    end += 1;
                } else {
                    break;
                }
            }
            if end.saturating_sub(start) < *minimum_length {
                cursor = end.max(start + prefix.len());
                continue;
            }
            output.replace_range(start..end, REDACTED);
            count = count.saturating_add(1);
            cursor = start + REDACTED.len();
        }
    }
    Redaction::new(output, count)
}

fn redact_auth_schemes(line: &str) -> Redaction<String> {
    let bearer = redact_after_ascii_marker(line, "bearer ");
    let basic = redact_after_ascii_marker(&bearer.value, "basic ");
    Redaction::new(basic.value, bearer.count.saturating_add(basic.count))
}

fn redact_after_ascii_marker(line: &str, marker: &str) -> Redaction<String> {
    let mut output = line.to_string();
    let mut count = 0usize;
    let mut cursor = 0usize;
    loop {
        let lower = output.to_ascii_lowercase();
        let Some(relative) = lower[cursor..].find(marker) else {
            break;
        };
        let start = cursor + relative + marker.len();
        let mut end = start;
        while let Some(byte) = output.as_bytes().get(end) {
            if is_secret_token_byte(*byte) {
                end += 1;
            } else {
                break;
            }
        }
        if end.saturating_sub(start) < 8 {
            cursor = end.max(start + 1);
            continue;
        }
        output.replace_range(start..end, REDACTED);
        count = count.saturating_add(1);
        cursor = start + REDACTED.len();
    }
    Redaction::new(output, count)
}

fn redact_url_credentials(line: &str) -> Redaction<String> {
    let mut output = line.to_string();
    let mut count = 0usize;
    let mut cursor = 0usize;
    while let Some(relative) = output[cursor..].find("://") {
        let authority_start = cursor + relative + 3;
        let authority_end = output.as_bytes()[authority_start..]
            .iter()
            .position(|byte| matches!(*byte, b'/' | b'?' | b'#') || byte.is_ascii_whitespace())
            .map(|relative_end| authority_start + relative_end)
            .unwrap_or(output.len());
        let Some(at_relative) = output[authority_start..authority_end].rfind('@') else {
            cursor = authority_end.max(authority_start + 1);
            continue;
        };
        let at = authority_start + at_relative;
        if at == authority_start {
            cursor = at + 1;
            continue;
        }
        output.replace_range(authority_start..at, REDACTED);
        count = count.saturating_add(1);
        cursor = authority_start + REDACTED.len() + 1;
    }
    Redaction::new(output, count)
}

fn redact_jwts(line: &str) -> Redaction<String> {
    let mut output = line.to_string();
    let mut count = 0usize;
    let mut cursor = 0usize;
    while cursor < output.len() {
        while cursor < output.len() && !is_jwt_byte(output.as_bytes()[cursor]) {
            cursor += 1;
        }
        let start = cursor;
        while cursor < output.len() && is_jwt_byte(output.as_bytes()[cursor]) {
            cursor += 1;
        }
        let end = cursor;
        if end <= start {
            continue;
        }
        let candidate = &output[start..end];
        let segments = candidate.split('.').collect::<Vec<_>>();
        if segments.len() == 3
            && segments[0].starts_with("eyJ")
            && segments.iter().all(|segment| segment.len() >= 8)
        {
            output.replace_range(start..end, REDACTED);
            count = count.saturating_add(1);
            cursor = start + REDACTED.len();
        }
    }
    Redaction::new(output, count)
}

fn is_secret_token_byte(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-' | b'.' | b'~' | b'+' | b'/' | b'=')
}

fn is_jwt_byte(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-' | b'.')
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn redacts_vendor_tokens_auth_urls_jwts_and_private_keys() {
        let leaked = concat!(
            "const harmless = 'ghp_abcdefghijklmnopqrstuvwxyz1234567890';\n",
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456\n",
            "DATABASE_URL=postgres://alice:password@localhost/app\n",
            "const jwt = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnopqrstuvwxyz';\n",
            "-----BEGIN PRIVATE KEY-----\nsecret material\n-----END PRIVATE KEY-----\n"
        );
        let redacted = redact_text(leaked);
        assert!(redacted.count >= 5);
        assert!(!redacted.value.contains("ghp_"));
        assert!(!redacted.value.contains("abcdefghijklmnopqrstuvwxyz123456"));
        assert!(!redacted.value.contains("alice:password"));
        assert!(!redacted.value.contains("eyJhbGci"));
        assert!(!redacted.value.contains("secret material"));
    }

    #[test]
    fn structured_redaction_preserves_token_metrics() {
        let value = json!({
            "token_budget": 8_000,
            "input_tokens": 512,
            "nested": {
                "accessToken": "live-access-token-value",
                "ordinary": "sk-proj-abcdefghijklmnopqrstuvwxyz1234567890"
            },
            "items": [{"cookie": "session=super-secret"}]
        });
        let redacted = redact_json_value(value);
        assert_eq!(redacted.value["token_budget"], 8_000);
        assert_eq!(redacted.value["input_tokens"], 512);
        assert_eq!(redacted.value["nested"]["accessToken"], REDACTED);
        assert_eq!(redacted.value["nested"]["ordinary"], REDACTED);
        assert_eq!(redacted.value["items"][0]["cookie"], REDACTED);
        assert_eq!(redacted.count, 3);
    }

    #[test]
    fn numeric_token_configuration_is_not_destroyed() {
        let source = "token_budget=8000\nmax_tokens: 4096\ntoken = 'secret-value'\n";
        let redacted = redact_text(source);
        assert!(redacted.value.contains("token_budget=8000"));
        assert!(redacted.value.contains("max_tokens: 4096"));
        assert!(!redacted.value.contains("secret-value"));
        assert_eq!(redacted.count, 1);
    }
}
