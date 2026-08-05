from pathlib import Path

path = Path('native/daemon/mcp/src/gateway.rs')
text = path.read_text(encoding='utf-8')
old = '''        if let Some(name) = line
            .strip_prefix("[mcp.")
            .and_then(|value| value.strip_suffix(']'))
        {
            validate_identifier(name, "backend")?;
            current = Some(name.to_string());
            sections.entry(name.to_string()).or_default();
            continue;
        }
'''
new = '''        if let Some(raw_name) = line
            .strip_prefix("[mcp.")
            .and_then(|value| value.strip_suffix(']'))
        {
            let name = parse_table_key(raw_name)?;
            validate_identifier(&name, "backend")?;
            current = Some(name.clone());
            sections.entry(name).or_default();
            continue;
        }
'''
if text.count(old) != 1:
    raise SystemExit(f'gateway table parser drifted: {text.count(old)}')
text = text.replace(old, new, 1)
marker = 'fn parse_string(value: &str) -> Result<String> {\n'
helper = '''fn parse_table_key(value: &str) -> Result<String> {
    let value = value.trim();
    if value.starts_with('\"') || value.ends_with('\"') {
        return parse_string(value);
    }
    Ok(value.to_string())
}

'''
if text.count(marker) != 1:
    raise SystemExit(f'gateway string parser insertion point drifted: {text.count(marker)}')
text = text.replace(marker, helper + marker, 1)
test_marker = '''    #[test]
    fn credentials_are_outside_the_workspace() {
'''
regression = '''    #[test]
    fn quoted_backend_table_keys_are_decoded_before_validation() {
        let parsed = parse_backends(
            r#"
[mcp."openai-docs"]
url = "https://developers.openai.com/mcp"
"#,
            "soleaux.toml",
        )
        .expect("quoted keys");
        assert_eq!(
            parsed
                .iter()
                .map(|backend| backend.name.as_str())
                .collect::<Vec<_>>(),
            vec!["openai-docs"]
        );
    }

    #[test]
    fn repository_gateway_configuration_with_quoted_key_is_discoverable() {
        let directory = tempdir().expect("tempdir");
        fs::write(
            directory.path().join("soleaux.toml"),
            "[mcp.\\\"openai-docs\\\"]\\nurl = \\\"https://developers.openai.com/mcp\\\"\\n",
        )
        .expect("configuration");
        let backends = discover_backends(directory.path()).expect("discover");
        assert_eq!(backends.len(), 1);
        assert_eq!(backends[0].name, "openai-docs");
        assert_eq!(backends[0].namespace, "openai-docs");
    }

'''
if text.count(test_marker) != 1:
    raise SystemExit(f'gateway regression insertion point drifted: {text.count(test_marker)}')
path.write_text(text.replace(test_marker, regression + test_marker, 1), encoding='utf-8')
