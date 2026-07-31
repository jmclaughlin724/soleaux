"""WorkspaceEdit normalization and process-local preview issuance."""

from __future__ import annotations

import dataclasses
import datetime
import difflib
import hashlib
import hmac
import json
import os
import pathlib
import secrets
import stat
import time

from pydantic import TypeAdapter, ValidationError

import soleaux.contracts.positions
import soleaux.contracts.repository
import soleaux.contracts.structural
import soleaux.contracts.workspace
import soleaux.editor.contracts
import soleaux.lsp.contracts
import soleaux.structural.snapshot

MAX_PREVIEWS = 256
MAX_EDIT_FILES = 256
MAX_TEXT_EDITS = 4096
MAX_REPLACEMENT_BYTES = 1024 * 1024
MAX_DIFF_BYTES = 64 * 1024
_OBJECT_MAPPING_ADAPTER = TypeAdapter(dict[str, object])
_OBJECT_LIST_ADAPTER = TypeAdapter(list[object])


class EditorPreviewError(ValueError):
    """An LSP edit cannot be represented by the safe preview contract."""


class PreviewLookupError(ValueError):
    """An apply request does not identify a live Soleaux preview."""


@dataclasses.dataclass(frozen=True, slots=True)
class NormalizedFileEdit:
    """Exact preimage, postimage, and patches for one existing file."""

    path: str
    preimage: bytes
    postimage: bytes
    preimage_hash: str
    postimage_hash: str
    patches: tuple[soleaux.editor.contracts.EditPatch, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class NormalizedWorkspaceEdit:
    """A completely validated set of existing-file text replacements."""

    files: tuple[NormalizedFileEdit, ...]
    patches: tuple[soleaux.editor.contracts.EditPatch, ...]
    diff: str
    diff_truncated: bool


@dataclasses.dataclass(frozen=True, slots=True)
class PreviewContext:
    """Minimal signed context needed to revalidate a stored preview."""

    workspace_id: str
    origin: str
    provider_name: str
    provider_config_digest: str
    project_id: str
    project_root: str
    project_config_digest: str
    compiler_identity: str
    provider_epoch: int


@dataclasses.dataclass(frozen=True, slots=True)
class StoredPreview:
    """Private mutation material retained only for one service lifespan."""

    payload: soleaux.editor.contracts.PreviewPayload
    root: pathlib.Path
    files: tuple[NormalizedFileEdit, ...]
    provider_config_digest: str
    project_id: str
    project_root: str
    project_config_digest: str
    compiler_identity: str
    expires_monotonic: float


class PreviewRegistry:
    """Bounded process-local preview registry authenticated by an ephemeral key."""

    def __init__(
        self,
        *,
        process_epoch: str,
        ttl_seconds: float = 300.0,
        max_previews: int = MAX_PREVIEWS,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("preview_ttl_seconds must be positive")
        if max_previews < 1:
            raise ValueError("max_previews must be positive")
        self._process_epoch = process_epoch
        self._ttl_seconds = ttl_seconds
        self._max_previews = max_previews
        self._key = secrets.token_bytes(32)
        self._previews: dict[str, StoredPreview] = {}
        self._consumed: set[str] = set()

    def issue(
        self,
        *,
        workspace_id: str,
        root: pathlib.Path,
        provider_name: str,
        provider_config_digest: str,
        project_id: str,
        project_root: str,
        project_config_digest: str,
        compiler_identity: str,
        provider_epoch: int,
        generation_fingerprint: str,
        operation: str,
        target: dict[str, object],
        position_encoding: str,
        normalized: NormalizedWorkspaceEdit,
        origin: str = "lsp",
        engine_version: str | None = None,
        rule_digest: str | None = None,
    ) -> soleaux.editor.contracts.PreviewPayload:
        """Authenticate and retain one no-write normalized preview."""
        self._prune()
        issued_at = datetime.datetime.now(datetime.UTC)
        expires_at = issued_at + datetime.timedelta(seconds=self._ttl_seconds)
        preview_id = secrets.token_urlsafe(24)
        unsigned: dict[str, object] = {
            "schema_version": "soleaux.preview/v1",
            "preview_id": preview_id,
            "workspace_id": workspace_id,
            "process_epoch": self._process_epoch,
            "origin": origin,
            "provider_name": provider_name,
            "provider_epoch": provider_epoch,
            "engine_version": engine_version,
            "rule_digest": rule_digest,
            "generation_fingerprint": generation_fingerprint,
            "operation": operation,
            "target": target,
            "affected_paths": [item.path for item in normalized.files],
            "preimage_hashes": {item.path: item.preimage_hash for item in normalized.files},
            "postimage_hashes": {item.path: item.postimage_hash for item in normalized.files},
            "position_encoding": position_encoding,
            "issued_at": issued_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "patches": [patch.model_dump(mode="json") for patch in normalized.patches],
            "diff": normalized.diff,
            "diff_truncated": normalized.diff_truncated,
        }
        digest = hmac.new(
            self._key,
            _canonical_json(unsigned),
            hashlib.sha256,
        ).hexdigest()
        payload = soleaux.editor.contracts.PreviewPayload.model_validate(
            {**unsigned, "digest": digest}
        )
        self._previews[preview_id] = StoredPreview(
            payload=payload,
            root=root,
            files=normalized.files,
            provider_config_digest=provider_config_digest,
            project_id=project_id,
            project_root=project_root,
            project_config_digest=project_config_digest,
            compiler_identity=compiler_identity,
            expires_monotonic=time.monotonic() + self._ttl_seconds,
        )
        while len(self._previews) > self._max_previews:
            oldest = next(iter(self._previews))
            self._previews.pop(oldest, None)
            self._consumed.discard(oldest)
        return payload

    def context(self, preview_id: str, digest: str) -> PreviewContext:
        """Return signed provider context without consuming the preview."""
        record = self._verified(preview_id, digest)
        return PreviewContext(
            workspace_id=record.payload.workspace_id,
            origin=record.payload.origin,
            provider_name=record.payload.provider_name,
            provider_config_digest=record.provider_config_digest,
            project_id=record.project_id,
            project_root=record.project_root,
            project_config_digest=record.project_config_digest,
            compiler_identity=record.compiler_identity,
            provider_epoch=record.payload.provider_epoch,
        )

    def claim(
        self,
        *,
        preview_id: str,
        digest: str,
        workspace_id: str,
        current_process_epoch: str,
        current_provider_epoch: int,
    ) -> StoredPreview:
        """Consume one preview after epoch, workspace, and expiry revalidation."""
        record = self._verified(preview_id, digest)
        if preview_id in self._consumed:
            raise PreviewLookupError("preview was already consumed")
        if record.payload.workspace_id != workspace_id:
            raise PreviewLookupError("preview belongs to another workspace")
        if record.payload.process_epoch != current_process_epoch:
            raise PreviewLookupError("preview belongs to another process epoch")
        if record.payload.provider_epoch != current_provider_epoch:
            raise PreviewLookupError("preview provider epoch is stale")
        self._consumed.add(preview_id)
        return record

    def clear(self) -> None:
        """Discard all mutation material at lifespan exit."""
        self._previews.clear()
        self._consumed.clear()

    def _verified(self, preview_id: str, digest: str) -> StoredPreview:
        record = self._previews.get(preview_id)
        if record is None:
            raise PreviewLookupError("preview is unknown or from another process")
        if not hmac.compare_digest(record.payload.digest, digest):
            raise PreviewLookupError("preview digest is invalid")
        if time.monotonic() > record.expires_monotonic:
            self._previews.pop(preview_id, None)
            self._consumed.discard(preview_id)
            raise PreviewLookupError("preview expired")
        return record

    def _prune(self) -> None:
        now = time.monotonic()
        expired = [
            preview_id
            for preview_id, record in self._previews.items()
            if now > record.expires_monotonic
        ]
        for preview_id in expired:
            self._previews.pop(preview_id, None)
            self._consumed.discard(preview_id)


def normalize_workspace_edit(
    raw_edit: object,
    *,
    root: pathlib.Path,
    bundle: soleaux.structural.snapshot.SnapshotBundle,
    position_encoding: str,
    document_versions: dict[str, int],
) -> NormalizedWorkspaceEdit:
    """Normalize both WorkspaceEdit variants into safe existing-file patches."""
    try:
        encoding = soleaux.contracts.positions.PositionEncoding(position_encoding)
    except ValueError as exc:
        raise EditorPreviewError(
            f"unsupported negotiated position encoding {position_encoding!r}"
        ) from exc
    edit = _object_mapping(raw_edit, label="WorkspaceEdit")
    unknown_keys = set(edit) - {"changes", "documentChanges", "changeAnnotations"}
    if unknown_keys:
        raise EditorPreviewError(f"WorkspaceEdit contains unsupported keys: {sorted(unknown_keys)}")
    annotations = edit.get("changeAnnotations")
    if annotations not in (None, {}):
        raise EditorPreviewError("annotated WorkspaceEdit changes are unsupported")
    if edit.get("changes") is not None and edit.get("documentChanges") is not None:
        raise EditorPreviewError("WorkspaceEdit cannot mix changes and documentChanges")

    pending: dict[str, list[soleaux.editor.contracts.EditPatch]] = {}
    raw_changes = edit.get("changes")
    if raw_changes is not None:
        changes = _object_mapping(raw_changes, label="WorkspaceEdit.changes")
        for uri, raw_edits in changes.items():
            _collect_uri_edits(
                pending,
                uri=uri,
                raw_edits=raw_edits,
                root=root,
                bundle=bundle,
                encoding=encoding,
            )

    raw_document_changes = edit.get("documentChanges")
    if raw_document_changes is not None:
        document_changes = _object_list(
            raw_document_changes,
            label="WorkspaceEdit.documentChanges",
        )
        for item in document_changes:
            change = _object_mapping(item, label="documentChanges entry")
            if "textDocument" not in change:
                raise EditorPreviewError("create/delete/rename resource operations are unsupported")
            if set(change) - {"textDocument", "edits"}:
                raise EditorPreviewError("TextDocumentEdit contains unsupported keys")
            document = _object_mapping(
                change.get("textDocument"),
                label="TextDocumentEdit.textDocument",
            )
            if set(document) - {"uri", "version"}:
                raise EditorPreviewError("TextDocumentEdit identifier contains unsupported keys")
            uri = document.get("uri")
            if not isinstance(uri, str):
                raise EditorPreviewError("TextDocumentEdit URI must be a string")
            version = document.get("version")
            if version is not None:
                if isinstance(version, bool) or not isinstance(version, int):
                    raise EditorPreviewError("TextDocumentEdit version must be an integer or null")
                if document_versions.get(uri) != version:
                    raise EditorPreviewError(f"stale or unavailable document version for {uri!r}")
            _collect_uri_edits(
                pending,
                uri=uri,
                raw_edits=change.get("edits"),
                root=root,
                bundle=bundle,
                encoding=encoding,
            )

    if not pending:
        raise EditorPreviewError("WorkspaceEdit contains no text edits")
    if len(pending) > MAX_EDIT_FILES:
        raise EditorPreviewError(f"WorkspaceEdit exceeds {MAX_EDIT_FILES} files")

    normalized_files: list[NormalizedFileEdit] = []
    all_patches: list[soleaux.editor.contracts.EditPatch] = []
    replacement_bytes = 0
    for path in sorted(pending):
        patches = sorted(
            pending[path],
            key=lambda patch: (patch.start_byte, patch.end_byte, patch.new_text),
        )
        _reject_overlaps(path, patches)
        preimage = bundle.contents[path]
        live_path = admit_edit_path(root, path)
        live = read_regular_file(live_path)
        if live != preimage:
            raise EditorPreviewError(f"preimage drift detected for {path!r}")
        postimage = preimage
        for patch in reversed(patches):
            replacement = _encode_replacement(patch.new_text)
            replacement_bytes += len(replacement)
            if replacement_bytes > MAX_REPLACEMENT_BYTES:
                raise EditorPreviewError(
                    f"WorkspaceEdit exceeds {MAX_REPLACEMENT_BYTES} replacement bytes"
                )
            postimage = postimage[: patch.start_byte] + replacement + postimage[patch.end_byte :]
        file_edit = NormalizedFileEdit(
            path=path,
            preimage=preimage,
            postimage=postimage,
            preimage_hash=soleaux.contracts.repository.content_digest(preimage),
            postimage_hash=soleaux.contracts.repository.content_digest(postimage),
            patches=tuple(patches),
        )
        normalized_files.append(file_edit)
        all_patches.extend(patches)

    if len(all_patches) > MAX_TEXT_EDITS:
        raise EditorPreviewError(f"WorkspaceEdit exceeds {MAX_TEXT_EDITS} text edits")
    diff, truncated = _bounded_diff(tuple(normalized_files))
    return NormalizedWorkspaceEdit(
        files=tuple(normalized_files),
        patches=tuple(
            sorted(
                all_patches,
                key=lambda patch: (
                    patch.path,
                    patch.start_byte,
                    patch.end_byte,
                    patch.new_text,
                ),
            )
        ),
        diff=diff,
        diff_truncated=truncated,
    )


def admit_edit_path(root: pathlib.Path, path: str | pathlib.Path) -> pathlib.Path:
    """Admit one existing regular file while rejecting every symlink component."""
    raw_path = str(path)
    if "\x00" in raw_path:
        raise EditorPreviewError("edit paths cannot contain NUL bytes")
    try:
        workspace_root = root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise EditorPreviewError(f"workspace root is unavailable: {str(root)!r}") from exc
    try:
        workspace = soleaux.contracts.workspace.WorkspaceRoot("editor", workspace_root, "")
        repository_path = soleaux.contracts.repository.RepositoryPath.admit(workspace, path)
        lexical_relative = pathlib.Path(repository_path.value)
        candidate = repository_path.absolute(workspace)
    except ValueError as exc:
        raise EditorPreviewError(str(exc)) from exc

    current = workspace_root
    try:
        for part in lexical_relative.parts:
            current /= part
            current_stat = os.lstat(current)
            if stat.S_ISLNK(current_stat.st_mode):
                raise EditorPreviewError(f"symlink edit paths are unsupported: {str(path)!r}")
        resolved = candidate.resolve(strict=True)
        resolved_stat = resolved.stat(follow_symlinks=False)
    except FileNotFoundError as exc:
        raise EditorPreviewError(f"edit preimage is unavailable: {str(path)!r}") from exc
    except (OSError, RuntimeError) as exc:
        raise EditorPreviewError(f"edit path is unavailable: {str(path)!r}") from exc
    if resolved != workspace_root and workspace_root not in resolved.parents:
        raise EditorPreviewError(f"edit path escapes workspace: {str(path)!r}")
    if not stat.S_ISREG(resolved_stat.st_mode):
        raise EditorPreviewError(f"edit target is not a regular file: {str(path)!r}")
    return resolved


def read_regular_file(path: pathlib.Path) -> bytes:
    """Read one regular file without following a final-component symlink."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except (OSError, ValueError) as exc:
        raise EditorPreviewError(f"edit target cannot be read safely: {str(path)!r}") from exc
    try:
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise EditorPreviewError(f"edit target is not a regular file: {str(path)!r}")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                return stream.read()
        except OSError as exc:
            raise EditorPreviewError(f"edit target cannot be read safely: {str(path)!r}") from exc
    finally:
        os.close(descriptor)


def _collect_uri_edits(
    pending: dict[str, list[soleaux.editor.contracts.EditPatch]],
    *,
    uri: object,
    raw_edits: object,
    root: pathlib.Path,
    bundle: soleaux.structural.snapshot.SnapshotBundle,
    encoding: soleaux.contracts.positions.PositionEncoding,
) -> None:
    if not isinstance(uri, str):
        raise EditorPreviewError("WorkspaceEdit URI must be a string")
    path = _relative_path_for_uri(root, uri)
    if path not in bundle.contents:
        raise EditorPreviewError(f"exact preimage is unavailable for {path!r}")
    edits = _object_list(raw_edits, label="text edits")
    preimage = bundle.contents[path]
    preimage_hash = soleaux.contracts.repository.content_digest(preimage)
    codec = soleaux.contracts.positions.PositionCodec(preimage)
    selected = pending.setdefault(path, [])
    for raw_edit in edits:
        edit = _object_mapping(raw_edit, label="TextEdit")
        if "annotationId" in edit:
            raise EditorPreviewError("annotated text edits are unsupported")
        if set(edit) != {"range", "newText"}:
            raise EditorPreviewError("TextEdit must contain only range and newText")
        new_text = edit.get("newText")
        if not isinstance(new_text, str):
            raise EditorPreviewError("TextEdit.newText must be a string")
        _encode_replacement(new_text)
        try:
            edit_range = soleaux.lsp.contracts.LspRange.model_validate(edit.get("range"))
            start_byte = _position_to_byte(
                codec,
                content=preimage,
                line=edit_range.start.line,
                character=edit_range.start.character,
                encoding=encoding,
            )
            end_byte = _position_to_byte(
                codec,
                content=preimage,
                line=edit_range.end.line,
                character=edit_range.end.character,
                encoding=encoding,
            )
        except ValueError as exc:
            raise EditorPreviewError(f"invalid TextEdit range for {path!r}: {exc}") from exc
        if end_byte < start_byte:
            raise EditorPreviewError(f"TextEdit range ends before it starts for {path!r}")
        selected.append(
            soleaux.editor.contracts.EditPatch(
                path=path,
                range=edit_range,
                start_byte=start_byte,
                end_byte=end_byte,
                new_text=new_text,
                preimage_hash=preimage_hash,
            )
        )


def _position_to_byte(
    codec: soleaux.contracts.positions.PositionCodec,
    *,
    content: bytes,
    line: int,
    character: int,
    encoding: soleaux.contracts.positions.PositionEncoding,
) -> int:
    if encoding is soleaux.contracts.positions.PositionEncoding.UTF16:
        return codec.point_to_byte(line, character, encoding=encoding)
    if encoding is soleaux.contracts.positions.PositionEncoding.UTF32:
        return codec.point_to_byte(line, character, encoding=encoding)
    line_start = codec.point_to_byte(line, 0)
    if line + 1 < codec.line_count:
        next_line = codec.point_to_byte(line + 1, 0)
        line_bytes = content[line_start:next_line]
    else:
        line_bytes = content[line_start:]
    body = line_bytes.removesuffix(b"\n").removesuffix(b"\r")
    if character < 0 or character > len(body):
        raise ValueError(
            f"utf-8 column {character} outside line {line} content ({len(body)} bytes)"
        )
    offset = line_start + character
    try:
        content[line_start:offset].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"utf-8 column {character} splits a code point on line {line}") from exc
    return offset


def _relative_path_for_uri(root: pathlib.Path, uri: str) -> str:
    workspace_root = root.resolve(strict=True)
    workspace = soleaux.contracts.workspace.WorkspaceRoot("editor", workspace_root, "")
    try:
        repository_path = soleaux.contracts.repository.RepositoryPath.admit(workspace, uri)
    except ValueError as exc:
        raise EditorPreviewError(str(exc)) from exc
    admit_edit_path(root, repository_path.value)
    return repository_path.value


def _reject_overlaps(path: str, patches: list[soleaux.editor.contracts.EditPatch]) -> None:
    previous: soleaux.editor.contracts.EditPatch | None = None
    for patch in patches:
        if previous is not None and (
            patch.start_byte < previous.end_byte or patch.start_byte == previous.start_byte
        ):
            raise EditorPreviewError(f"overlapping text edits for {path!r}")
        previous = patch


def _bounded_diff(files: tuple[NormalizedFileEdit, ...]) -> tuple[str, bool]:
    chunks: list[str] = []
    for item in files:
        chunks.extend(
            difflib.unified_diff(
                item.preimage.decode("utf-8").splitlines(keepends=True),
                item.postimage.decode("utf-8").splitlines(keepends=True),
                fromfile=f"a/{item.path}",
                tofile=f"b/{item.path}",
            )
        )
    encoded = "".join(chunks).encode("utf-8")
    if len(encoded) <= MAX_DIFF_BYTES:
        return encoded.decode("utf-8"), False
    clipped = encoded[:MAX_DIFF_BYTES]
    while True:
        try:
            return clipped.decode("utf-8") + "\n... diff truncated ...\n", True
        except UnicodeDecodeError as exc:
            clipped = clipped[: exc.start]


def _object_mapping(value: object, *, label: str) -> dict[str, object]:
    try:
        return _OBJECT_MAPPING_ADAPTER.validate_python(value, strict=True)
    except ValidationError:
        raise EditorPreviewError(f"{label} must be an object with string keys") from None


def _object_list(value: object, *, label: str) -> list[object]:
    try:
        return _OBJECT_LIST_ADAPTER.validate_python(value, strict=True)
    except ValidationError:
        raise EditorPreviewError(f"{label} must be an array") from None


def _encode_replacement(value: str) -> bytes:
    try:
        return value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise EditorPreviewError("TextEdit.newText is not valid UTF-8") from exc


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _byte_to_point(preimage: bytes, offset: int) -> tuple[int, int]:
    """Zero-based line and code-point column of one UTF-8 byte offset."""
    prefix = preimage[:offset]
    line = prefix.count(b"\n")
    last_newline = prefix.rfind(b"\n")
    return line, len(prefix[last_newline + 1 :].decode("utf-8"))


def normalize_byte_edits(
    *,
    bundle: soleaux.structural.snapshot.SnapshotBundle,
    edits: tuple[soleaux.contracts.structural.StructuralEdit, ...],
) -> NormalizedWorkspaceEdit:
    """Normalize engine byte edits into safe existing-file patches.

    Overlapping or out-of-bounds ranges and edits against uncaptured files
    fail before any preview is issued — a structural preview is complete or
    it does not exist.
    """
    if not edits:
        raise EditorPreviewError("engine returned no edits")
    if len({edit.path for edit in edits}) > MAX_EDIT_FILES or len(edits) > MAX_TEXT_EDITS:
        raise EditorPreviewError("structural rewrite exceeds the edit bounds")
    by_path: dict[str, list[soleaux.contracts.structural.StructuralEdit]] = {}
    for edit in edits:
        by_path.setdefault(edit.path, []).append(edit)
    files: list[NormalizedFileEdit] = []
    all_patches: list[soleaux.editor.contracts.EditPatch] = []
    for path in sorted(by_path):
        preimage = bundle.contents.get(path)
        if preimage is None:
            raise EditorPreviewError(f"{path}: edit targets an uncaptured file")
        preimage_hash = hashlib.sha256(preimage).hexdigest()
        patches: list[soleaux.editor.contracts.EditPatch] = []
        for edit in sorted(by_path[path], key=lambda item: (item.byte_start, item.byte_end)):
            if edit.byte_end < edit.byte_start or edit.byte_end > len(preimage):
                raise EditorPreviewError(f"{path}: edit range is out of bounds")
            start_line, start_character = _byte_to_point(preimage, edit.byte_start)
            end_line, end_character = _byte_to_point(preimage, edit.byte_end)
            patches.append(
                soleaux.editor.contracts.EditPatch(
                    path=path,
                    range=soleaux.lsp.contracts.LspRange(
                        start=soleaux.lsp.contracts.LspPosition(
                            line=start_line, character=start_character
                        ),
                        end=soleaux.lsp.contracts.LspPosition(
                            line=end_line, character=end_character
                        ),
                    ),
                    start_byte=edit.byte_start,
                    end_byte=edit.byte_end,
                    new_text=edit.inserted_text,
                    preimage_hash=preimage_hash,
                )
            )
        _reject_overlaps(path, patches)
        postimage = bytearray(preimage)
        for patch in reversed(patches):
            postimage[patch.start_byte : patch.end_byte] = _encode_replacement(patch.new_text)
        files.append(
            NormalizedFileEdit(
                path=path,
                preimage=preimage,
                postimage=bytes(postimage),
                preimage_hash=preimage_hash,
                postimage_hash=hashlib.sha256(bytes(postimage)).hexdigest(),
                patches=tuple(patches),
            )
        )
        all_patches.extend(patches)
    diff, diff_truncated = _bounded_diff(tuple(files))
    return NormalizedWorkspaceEdit(
        files=tuple(files),
        patches=tuple(all_patches),
        diff=diff,
        diff_truncated=diff_truncated,
    )
