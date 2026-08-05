from pathlib import Path

path = Path('scripts/build_unsigned_alpha.py')
text = path.read_text(encoding='utf-8')
old = '  "$BIN/soleaux" uninstall --preserve-state true\n'
new = '  "$BIN/soleaux" uninstall\n'
count = text.count(old)
if count != 1:
    raise SystemExit(f'alpha uninstall invocation drifted: {count}')
path.write_text(text.replace(old, new, 1), encoding='utf-8')
