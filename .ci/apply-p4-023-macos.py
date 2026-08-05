from pathlib import Path


def lines(*values: str) -> str:
    return "\n".join(values) + "\n"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if text.count(old) != 1:
        raise SystemExit(f"P4-023 repair target drifted: {label}")
    return text.replace(old, new, 1)


memory = Path("native/daemon/mcp/src/memory.rs")
text = memory.read_text(encoding="utf-8")
text = replace_once(
    text,
    lines(
        "    for memory_root in roots.iter().filter(|entry| entry.path.is_dir()) {",
        "        for entry in WalkBuilder::new(&memory_root.path)",
    ),
    lines(
        "    for memory_root in roots.iter().filter(|entry| entry.path.is_dir()) {",
        "        let canonical_root = match fs::canonicalize(&memory_root.path) {",
        "            Ok(value) => value,",
        "            Err(_) => {",
        "                push_gap(",
        "                    gaps,",
        "                    seen_gaps,",
        "                    gap(",
        "                        \"memory_root_unavailable\",",
        "                        \"An attached memory root could not be canonicalized and was omitted.\",",
        "                        \"warning\",",
        "                        true,",
        "                        Some(\"memory\"),",
        "                        Some(&memory_root.path.to_string_lossy()),",
        "                    ),",
        "                );",
        "                continue;",
        "            }",
        "        };",
        "        for entry in WalkBuilder::new(&canonical_root)",
    ),
    "canonical memory root",
)
text = replace_once(
    text,
    "                .strip_prefix(&memory_root.path)\n",
    "                .strip_prefix(&canonical_root)\n",
    "canonical relative path",
)
text = replace_once(
    text,
    "                root: memory_root.path.clone(),\n",
    "                root: canonical_root.clone(),\n",
    "canonical candidate root",
)
memory.write_text(text, encoding="utf-8")

mcp = Path("native/daemon/mcp/src/lib.rs")
text = mcp.read_text(encoding="utf-8")
old = lines(
    "        fs::write(&source_path, \"export function newState() { return 'new'; }\")",
    "            .expect(\"external mutation\");",
    "        let new_after = server",
    "            .call_async(\"code.search\", &json!({\"query\": \"newState\"}))",
    "            .await",
    "            .expect(\"new search\");",
    "        assert!(",
    "            new_after",
    "                .data",
    "                .get(\"matches\")",
    "                .and_then(Value::as_array)",
    "                .is_some_and(|matches| !matches.is_empty())",
    "        );",
)
new = lines(
    "        fs::write(&source_path, \"export function newState() { return 'new'; }\")",
    "            .expect(\"external mutation\");",
    "        let mut new_state_observed = false;",
    "        for _ in 0..100 {",
    "            let candidate = server",
    "                .call_async(\"code.search\", &json!({\"query\": \"newState\"}))",
    "                .await",
    "                .expect(\"new search\");",
    "            if candidate",
    "                .data",
    "                .get(\"matches\")",
    "                .and_then(Value::as_array)",
    "                .is_some_and(|matches| !matches.is_empty())",
    "            {",
    "                new_state_observed = true;",
    "                break;",
    "            }",
    "            tokio::time::sleep(std::time::Duration::from_millis(10)).await;",
    "        }",
    "        assert!(",
    "            new_state_observed,",
    "            \"watcher-backed refresh did not observe the external mutation\"",
    "        );",
)
text = replace_once(text, old, new, "macOS watcher observation")
mcp.write_text(text, encoding="utf-8")
