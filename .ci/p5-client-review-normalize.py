from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(f"normalization target drifted for {label}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


probe = "native/scripts/probe_client_capabilities.py"
replace_once(
    probe,
    "import argparse\n",
    "import argparse\nimport contextlib\n",
    "contextlib import",
)
replace_once(
    probe,
    '                try:\n'
    '                    process.kill()\n'
    '                except ProcessLookupError:\n'
    '                    pass\n',
    '                with contextlib.suppress(ProcessLookupError):\n'
    '                    process.kill()\n',
    "bounded process termination",
)

validator = "native/scripts/validate_client_capability_matrix.py"
replace_once(
    validator,
    '            if parsed.scheme != "https" or parsed.hostname != "github.com" or parsed.path != expected_path:\n'
    '                fail("OpenCode asset URL is not pinned to the exact matrix version")\n',
    '            exact_asset = (\n'
    '                parsed.scheme == "https"\n'
    '                and parsed.hostname == "github.com"\n'
    '                and parsed.path == expected_path\n'
    '            )\n'
    '            if not exact_asset:\n'
    '                fail("OpenCode asset URL is not pinned to the exact matrix version")\n',
    "OpenCode exact asset validation",
)
