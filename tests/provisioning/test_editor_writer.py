"""Editor writer: targeted settings.json key replacement preserves the rest of the file."""

from __future__ import annotations

import pathlib

import soleaux.provisioning.editor_writer


def test_disable_python_language_server_replaces_value(tmp_path: pathlib.Path) -> None:
    settings = tmp_path / ".vscode" / "settings.json"
    settings.parent.mkdir()
    settings.write_text('{"python.languageServer": "Pylance"}', encoding="utf-8")

    changed = soleaux.provisioning.editor_writer.disable_editor_setting(
        settings, "python.languageServer", "None"
    )

    assert changed is True
    assert '"python.languageServer": null' in settings.read_text(encoding="utf-8")


def test_disable_preserves_unrelated_keys_and_comments(tmp_path: pathlib.Path) -> None:
    settings = tmp_path / ".vscode" / "settings.json"
    settings.parent.mkdir()
    original = (
        "// user comment\n"
        "{\n"
        '  "editor.tabSize": 2,\n'
        '  "python.languageServer": "Pylance",\n'
        '  "files.exclude": {"**/.git": true}\n'
        "}\n"
    )
    settings.write_text(original, encoding="utf-8")

    changed = soleaux.provisioning.editor_writer.disable_editor_setting(
        settings, "python.languageServer", "None"
    )

    assert changed is True
    updated = settings.read_text(encoding="utf-8")
    assert "// user comment" in updated
    assert '"editor.tabSize": 2' in updated
    assert '"files.exclude": {"**/.git": true}' in updated
    assert '"python.languageServer": null' in updated


def test_disable_is_idempotent_when_already_disabled(tmp_path: pathlib.Path) -> None:
    settings = tmp_path / ".vscode" / "settings.json"
    settings.parent.mkdir()
    settings.write_text('{"python.analysis.indexing": false}', encoding="utf-8")

    changed = soleaux.provisioning.editor_writer.disable_editor_setting(
        settings, "python.analysis.indexing", "false"
    )

    assert changed is False


def test_disable_returns_false_when_key_absent(tmp_path: pathlib.Path) -> None:
    settings = tmp_path / ".vscode" / "settings.json"
    settings.parent.mkdir()
    settings.write_text('{"editor.tabSize": 2}', encoding="utf-8")

    changed = soleaux.provisioning.editor_writer.disable_editor_setting(
        settings, "python.languageServer", "None"
    )

    assert changed is False


def test_disable_changes_only_the_top_level_key(tmp_path: pathlib.Path) -> None:
    settings = tmp_path / ".vscode" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(
        '{"nested":{"python.languageServer":"Nested"},"python.languageServer":"Pylance"}',
        encoding="utf-8",
    )

    changed = soleaux.provisioning.editor_writer.disable_editor_setting(
        settings, "python.languageServer", "None"
    )

    assert changed is True
    assert settings.read_text(encoding="utf-8") == (
        '{"nested":{"python.languageServer":"Nested"},"python.languageServer":null}'
    )


def test_disable_rejects_malformed_jsonc_without_textual_fallback(tmp_path: pathlib.Path) -> None:
    settings = tmp_path / ".vscode" / "settings.json"
    settings.parent.mkdir()
    original = '{"python.languageServer":'
    settings.write_text(original, encoding="utf-8")

    changed = soleaux.provisioning.editor_writer.disable_editor_setting(
        settings, "python.languageServer", "None"
    )

    assert changed is False
    assert settings.read_text(encoding="utf-8") == original


def test_disable_returns_false_when_file_missing(tmp_path: pathlib.Path) -> None:
    settings = tmp_path / ".vscode" / "settings.json"

    changed = soleaux.provisioning.editor_writer.disable_editor_setting(
        settings, "python.languageServer", "None"
    )

    assert changed is False


def test_disable_preserves_trailing_comma_state_when_no_comma_present(
    tmp_path: pathlib.Path,
) -> None:
    settings = tmp_path / ".vscode" / "settings.json"
    settings.parent.mkdir()
    settings.write_text('{\n  "python.languageServer": "Pylance"\n}\n', encoding="utf-8")

    soleaux.provisioning.editor_writer.disable_editor_setting(
        settings, "python.languageServer", "None"
    )

    updated = settings.read_text(encoding="utf-8")
    # Original had no trailing comma; replacement should not add one.
    assert "null\n}" in updated
    assert "null,}" not in updated
