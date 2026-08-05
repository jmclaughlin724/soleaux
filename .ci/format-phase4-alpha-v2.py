from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"alpha formatting target drifted for {label}: {count}")
    return text.replace(old, new, 1)


build = Path("scripts/build_unsigned_alpha.py")
text = build.read_text(encoding="utf-8")
text = replace_once(
    text,
    '        "workspaceMembers": sorted(id_to_key[str(value)] for value in metadata.get("workspace_members", [])),\n',
    '        "workspaceMembers": sorted(\n'
    '            id_to_key[str(value)]\n'
    '            for value in metadata.get("workspace_members", [])\n'
    '        ),\n',
    "workspace members",
)
text = replace_once(
    text,
    '        if member.isdir():\n'
    '            member.mode = 0o755\n'
    '        elif member.name.endswith(("/bin/soleaux", "/bin/soleauxd", "/install.sh", "/uninstall.sh")):\n'
    '            member.mode = 0o755\n',
    '        executable_suffixes = (\n'
    '            "/bin/soleaux",\n'
    '            "/bin/soleauxd",\n'
    '            "/install.sh",\n'
    '            "/uninstall.sh",\n'
    '        )\n'
    '        if member.isdir() or member.name.endswith(executable_suffixes):\n'
    '            member.mode = 0o755\n',
    "tar modes",
)
text = replace_once(
    text,
    'def add_sorted(archive: tarfile.TarFile, root: pathlib.Path, package_name: str, epoch: int) -> None:\n'
    '    archive.add(root, arcname=package_name, recursive=False, filter=tar_filter(epoch))\n'
    '    for path in sorted(root.rglob("*"), key=lambda candidate: candidate.relative_to(root).as_posix()):\n',
    'def add_sorted(\n'
    '    archive: tarfile.TarFile,\n'
    '    root: pathlib.Path,\n'
    '    package_name: str,\n'
    '    epoch: int,\n'
    ') -> None:\n'
    '    archive.add(root, arcname=package_name, recursive=False, filter=tar_filter(epoch))\n'
    '    paths = sorted(\n'
    '        root.rglob("*"),\n'
    '        key=lambda candidate: candidate.relative_to(root).as_posix(),\n'
    '    )\n'
    '    for path in paths:\n',
    "sorted traversal",
)
text = replace_once(
    text,
    '    if len(source_commit) != 40 or any(character not in "0123456789abcdef" for character in source_commit):\n',
    '    invalid_commit = len(source_commit) != 40 or any(\n'
    '        character not in "0123456789abcdef" for character in source_commit\n'
    '    )\n'
    '    if invalid_commit:\n',
    "source commit validation",
)
text = replace_once(
    text,
    'Run `./install.sh` to copy `soleaux` and `soleauxd` into `${{SOLEAUX_INSTALL_BIN:-$HOME/.local/bin}}` and write a per-user service manifest. The installer does not mutate Claude, Codex, OpenCode, Cursor, or other vendor-native stores.\n',
    'Run `./install.sh` to copy `soleaux` and `soleauxd` into\n'
    '`${{SOLEAUX_INSTALL_BIN:-$HOME/.local/bin}}` and write a per-user service manifest.\n'
    'The installer does not mutate Claude, Codex, OpenCode, Cursor, or other\n'
    'vendor-native stores.\n',
    "install guidance",
)
text = replace_once(
    text,
    'Run `soleaux service start` after reviewing the generated per-user service manifest. Use `soleaux doctor`, `soleaux service status`, `soleaux backup`, `soleaux export`, and `soleaux repair` for operational checks.\n',
    'Run `soleaux service start` after reviewing the generated per-user service\n'
    'manifest. Use `soleaux doctor`, `soleaux service status`, `soleaux backup`,\n'
    '`soleaux export`, and `soleaux repair` for operational checks.\n',
    "service guidance",
)
text = replace_once(
    text,
    'Run `./uninstall.sh` to call `soleaux uninstall --preserve-state true`. State removal requires an explicit separate decision.\n',
    'Run `./uninstall.sh` to call `soleaux uninstall`. State removal requires an\n'
    'explicit separate decision.\n',
    "uninstall guidance",
)
build.write_text(text, encoding="utf-8")

smoke = Path("scripts/smoke_unsigned_alpha.py")
text = smoke.read_text(encoding="utf-8")
text = replace_once(
    text,
    '        raise RuntimeError(\n'
    '            f"{name} failed with {result.returncode}: {result.stderr.strip() or result.stdout.strip()}"\n'
    '        )\n',
    '        detail = result.stderr.strip() or result.stdout.strip()\n'
    '        raise RuntimeError(\n'
    '            f"{name} failed with {result.returncode}: {detail}"\n'
    '        )\n',
    "command failure",
)
text = replace_once(
    text,
    '    except subprocess.TimeoutExpired:\n'
    '        process.kill()\n'
    '        code = process.wait(timeout=10)\n'
    '        raise RuntimeError(f"daemon did not stop after graceful IPC shutdown; killed with {code}")\n',
    '    except subprocess.TimeoutExpired as error:\n'
    '        process.kill()\n'
    '        code = process.wait(timeout=10)\n'
    '        raise RuntimeError(\n'
    '            "daemon did not stop after graceful IPC shutdown; "\n'
    '            f"killed with {code}"\n'
    '        ) from error\n',
    "timeout chaining",
)
smoke.write_text(text, encoding="utf-8")
