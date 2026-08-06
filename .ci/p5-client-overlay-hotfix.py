#!/usr/bin/env python3
from pathlib import Path

probe = Path("native/scripts/probe_client_capabilities.py")
text = probe.read_text(encoding="utf-8")
text = text.replace(
    'VERSION_PATTERN = re.compile(r"(?<!\\d)(\\d+\\.\\d+\\.\\d+(?:[-+][0-9A-Za-z.-]+)?)")\n',
    'VERSION_PATTERN = re.compile(r"(?<!\\d)(\\d+\\.\\d+\\.\\d+(?:[-+][0-9A-Za-z.-]+)?)")\n'
    'ANSI_PATTERN = re.compile(r"\\x1b\\[[0-?]*[ -/]*[@-~]")\n',
)
old = '''    combined = f"{result['stdout']}\\n{result['stderr']}"
    missing = [token for token in expected if token not in combined]
'''
new = '''    combined = ANSI_PATTERN.sub(
        "", f"{result['stdout']}\\n{result['stderr']}"
    ).casefold()
    missing = [token for token in expected if token.casefold() not in combined]
'''
if text.count(old) != 1:
    raise SystemExit("signal normalization target drifted")
probe.write_text(text.replace(old, new, 1), encoding="utf-8")

tests = Path("tests/test_client_capability_matrix.py")
test_text = tests.read_text(encoding="utf-8")
regression = '''

def test_probe_normalizes_ansi_and_case_for_opencode(tmp_path: Path) -> None:
    binary = tmp_path / "fake-opencode"
    binary.write_text(
        "#!/usr/bin/env python3\\n"
        "import sys\\n"
        "args = sys.argv[1:]\\n"
        "if args == ['--version']:\\n"
        "    print('1.18.14')\\n"
        "elif args == ['serve', '--help']:\\n"
        "    print('STARTS A HEADLESS OPENCODE SERVER', file=sys.stderr)\\n"
        "else:\\n"
        "    print('\\x1b[36mCOMMANDS:\\x1b[0m opencode serve', file=sys.stderr)\\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    output = tmp_path / "opencode-probe.json"
    result = run(
        str(PROBE),
        "--platform",
        "opencode",
        "--binary",
        str(binary),
        "--output",
        str(output),
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "pass"
'''
if "test_probe_normalizes_ansi_and_case_for_opencode" not in test_text:
    test_text += regression
tests.write_text(test_text, encoding="utf-8")
