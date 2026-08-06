#!/usr/bin/env python3
from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "native/scripts/probe_client_capabilities.py",
    "import contextlib\n\nimport argparse\n",
    "import argparse\nimport contextlib\n",
    "sort probe imports",
)
replace_once(
    "native/scripts/validate_client_capability_matrix.py",
    '    result = run_self_tests(matrix) if arguments.self_test else validate_matrix(matrix, arguments.platform)\n',
    '    result = (\n'
    '        run_self_tests(matrix)\n'
    '        if arguments.self_test\n'
    '        else validate_matrix(matrix, arguments.platform)\n'
    '    )\n',
    "wrap validator dispatch",
)
replace_once(
    ".github/workflows/client-capability-matrix.yml",
    '''          npm install --global "$CLAUDE_ARCHIVE" --ignore-scripts
          python3 native/scripts/probe_client_capabilities.py \\
            --platform claude_code --binary claude \\
            --output /tmp/claude-code.json
''',
    '''          # The archive digest and NPM integrity were verified immediately above.
          # Run its isolated postinstall to materialize the pinned native Linux binary.
          npm install --global "$CLAUDE_ARCHIVE"
          claude --version | tee /tmp/claude-installed-version.txt
          python3 native/scripts/probe_client_capabilities.py \\
            --platform claude_code --binary claude \\
            --output /tmp/claude-code.json
''',
    "install verified Claude native binary",
)
