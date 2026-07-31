"""soleaux.preview/v1 structural rewrites: preview, apply, and fail-closed paths."""

from __future__ import annotations

import pathlib

import soleaux.analysis.service
import soleaux.contracts.requests
import soleaux.contracts.results
import soleaux.contracts.structural


def _pattern(fix: str | None = "debug.trace($ARG)") -> soleaux.contracts.structural.InlinePattern:
    return soleaux.contracts.structural.InlinePattern(
        language="TypeScript", pattern="console.log($ARG)", fix=fix
    )


async def test_structural_rewrite_previews_and_applies_across_files(tmp_path: pathlib.Path) -> None:
    first = tmp_path / "src" / "a.ts"
    second = tmp_path / "src" / "b.ts"
    first.parent.mkdir(parents=True)
    first.write_text("// café\nconsole.log(alpha);\n", encoding="utf-8")
    second.write_text("console.log(beta);\nconst keep = 1;\n", encoding="utf-8")

    async with soleaux.analysis.service.SoleauxService.from_root(tmp_path) as service:
        previewed = await service.preview(
            soleaux.contracts.requests.PreviewEditRequest(
                operation=soleaux.contracts.requests.PreviewOperation.STRUCTURAL_REWRITE,
                structural=_pattern(),
            )
        )
        assert previewed.status is soleaux.contracts.results.ResultStatus.OK, previewed.error
        payload = previewed.data
        assert payload is not None
        assert payload["origin"] == "structural"
        assert payload["provider_name"] == "structural:python"
        assert payload["engine_version"] == "0.44.1"
        assert sorted(payload["affected_paths"]) == ["src/a.ts", "src/b.ts"]
        assert "debug.trace(alpha)" in payload["diff"]

        applied = await service.apply(
            soleaux.contracts.requests.ApplyEditRequest(
                preview_id=payload["preview_id"],
                digest=payload["digest"],
                confirm=True,
            )
        )
        assert applied.status is soleaux.contracts.results.ResultStatus.OK, applied.error
        assert applied.data is not None
        assert applied.data["state"] == "applied"

    assert first.read_text(encoding="utf-8") == "// café\ndebug.trace(alpha);\n"
    assert second.read_text(encoding="utf-8") == "debug.trace(beta);\nconst keep = 1;\n"


async def test_structural_rewrite_reports_no_changes_without_matches(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "main.ts").write_text("const value = 1;\n", encoding="utf-8")

    async with soleaux.analysis.service.SoleauxService.from_root(tmp_path) as service:
        previewed = await service.preview(
            soleaux.contracts.requests.PreviewEditRequest(
                operation=soleaux.contracts.requests.PreviewOperation.STRUCTURAL_REWRITE,
                structural=_pattern(),
            )
        )
        assert previewed.status is soleaux.contracts.results.ResultStatus.OK
        assert previewed.data is not None
        assert previewed.data["state"] == "no_changes"


async def test_structural_rewrite_requires_an_explicit_fix(tmp_path: pathlib.Path) -> None:
    (tmp_path / "main.ts").write_text("console.log(1);\n", encoding="utf-8")

    async with soleaux.analysis.service.SoleauxService.from_root(tmp_path) as service:
        previewed = await service.preview(
            soleaux.contracts.requests.PreviewEditRequest(
                operation=soleaux.contracts.requests.PreviewOperation.STRUCTURAL_REWRITE,
                structural=_pattern(fix=None),
            )
        )
        assert previewed.status is soleaux.contracts.results.ResultStatus.ERROR
        assert previewed.error is not None
        assert "explicit fix" in previewed.error.message


async def test_structural_rewrite_rejects_unknown_rule_references(tmp_path: pathlib.Path) -> None:
    (tmp_path / "main.ts").write_text("console.log(1);\n", encoding="utf-8")

    async with soleaux.analysis.service.SoleauxService.from_root(tmp_path) as service:
        previewed = await service.preview(
            soleaux.contracts.requests.PreviewEditRequest(
                operation=soleaux.contracts.requests.PreviewOperation.STRUCTURAL_REWRITE,
                structural=soleaux.contracts.structural.RuleReference(rule_id="not-a-rule"),
            )
        )
        assert previewed.status is soleaux.contracts.results.ResultStatus.ERROR
        assert previewed.error is not None
        assert previewed.error.error_type == "unknown_rule"
