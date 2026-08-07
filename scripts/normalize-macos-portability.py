#!/usr/bin/env python3
"""Apply the exact source normalizations required by the macOS build target."""

from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_in_section(
    path: str,
    start_marker: str,
    end_marker: str,
    old: str,
    new: str,
    label: str,
) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    start = text.find(start_marker)
    if start < 0:
        raise SystemExit(f"{label}: start marker not found")
    end = text.find(end_marker, start)
    if end < 0:
        raise SystemExit(f"{label}: end marker not found")
    section = text[start:end]
    count = section.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one section match, found {count}")
    section = section.replace(old, new, 1)
    target.write_text(text[:start] + section + text[end:], encoding="utf-8")


replace_once(
    "native/daemon/vault/src/keyring.rs",
    """use std::{
    collections::BTreeMap,
    fs,
    io::Write,
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::{Arc, Mutex},
};
""",
    """use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
    process::Command,
    sync::{Arc, Mutex},
};
#[cfg(target_os = "linux")]
use std::{io::Write, process::Stdio};
""",
    "keyring platform imports",
)

replace_once(
    "native/daemon/ipc/src/paths.rs",
    """        return Ok(user_home()?
            .join("Library")
            .join("Application Support")
            .join("Soleaux"));
""",
    """        Ok(user_home()?
            .join("Library")
            .join("Application Support")
            .join("Soleaux"))
""",
    "macOS Soleaux home expression",
)

replace_once(
    "native/daemon/ipc/src/paths.rs",
    """        return Ok(user_home()?
            .join("Library")
            .join("LaunchAgents")
            .join("com.soleaux.daemon.plist"));
""",
    """        Ok(user_home()?
            .join("Library")
            .join("LaunchAgents")
            .join("com.soleaux.daemon.plist"))
""",
    "macOS LaunchAgent expression",
)

service_path = "native/daemon/ipc/src/service.rs"
replace_in_section(
    service_path,
    "pub fn render_manifest(",
    "fn platform_start(",
    "        return Ok(format!(\n",
    "        Ok(format!(\n",
    "macOS manifest return expression",
)
replace_in_section(
    service_path,
    "pub fn render_manifest(",
    "fn platform_start(",
    "        ));\n    }\n    #[cfg(all(unix, not(target_os = \"macos\")))]",
    "        ))\n    }\n    #[cfg(all(unix, not(target_os = \"macos\")))]",
    "macOS manifest closing expression",
)
replace_in_section(
    service_path,
    "fn platform_start(",
    "fn platform_stop(",
    "        return Ok(());\n",
    "        Ok(())\n",
    "macOS service start expression",
)
replace_in_section(
    service_path,
    "fn platform_stop(",
    "fn run_status(",
    "        return Ok(());\n",
    "        Ok(())\n",
    "macOS service stop expression",
)
