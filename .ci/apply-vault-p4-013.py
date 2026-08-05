from pathlib import Path


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label} drifted: expected 1 occurrence, observed {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


keyring = Path("native/daemon/vault/src/keyring.rs")
source_lines = keyring.read_text(encoding="utf-8").splitlines()
targets = [
    index
    for index, line in enumerate(source_lines)
    if 'format!("\'{}\'", value.replace(' in line
]
if len(targets) != 1:
    raise SystemExit(
        f"PowerShell single-quote escaping drifted: expected 1 target, observed {len(targets)}"
    )
source_lines[targets[0]] = (
    "    format!(\"'{}'\", value.replace('" + chr(92) + "'', \"''\"))"
)
keyring.write_text("\n".join(source_lines) + "\n", encoding="utf-8")

vault = Path("native/daemon/vault/src/vault.rs")
replace_once(
    vault,
    "    io::{Read, Write},\n",
    "    io::Write,\n",
    "unused vault Read import",
)

workspace = Path("native/Cargo.toml")
text = workspace.read_text(encoding="utf-8")
old = '  "daemon/storage",\n  "apps/cli",'
new = '  "daemon/storage",\n  "daemon/vault",\n  "apps/cli",'
count = text.count(old)
if count != 2:
    raise SystemExit(f"vault workspace member targets drifted: expected 2, observed {count}")
workspace.write_text(text.replace(old, new), encoding="utf-8")
