"""D031: LSP capability and document-synchronization contracts."""

import soleaux.lsp.contracts


def test_normalize_text_document_sync_numeric_and_object_forms() -> None:
    assert (
        soleaux.lsp.contracts.normalize_text_document_sync(0)
        is soleaux.lsp.contracts.TextDocumentSyncKind.NONE
    )
    assert (
        soleaux.lsp.contracts.normalize_text_document_sync(1)
        is soleaux.lsp.contracts.TextDocumentSyncKind.FULL
    )
    assert (
        soleaux.lsp.contracts.normalize_text_document_sync(2)
        is soleaux.lsp.contracts.TextDocumentSyncKind.INCREMENTAL
    )
    assert (
        soleaux.lsp.contracts.normalize_text_document_sync({"openClose": True, "change": 2})
        is soleaux.lsp.contracts.TextDocumentSyncKind.INCREMENTAL
    )


def test_server_capabilities_normalize_camel_case_payload() -> None:
    capabilities = soleaux.lsp.contracts.ServerCapabilities.from_lsp(
        {
            "textDocumentSync": {"openClose": True, "change": 2},
            "definitionProvider": True,
            "diagnosticProvider": {
                "interFileDependencies": True,
                "workspaceDiagnostics": False,
            },
            "renameProvider": {"prepareProvider": True},
            "positionEncoding": "utf-16",
        }
    )

    assert capabilities.text_document_sync is soleaux.lsp.contracts.TextDocumentSyncKind.INCREMENTAL
    assert capabilities.open_close is True
    assert capabilities.definition_provider is True
    assert capabilities.diagnostic_provider is True
    assert capabilities.rename_provider is True
    assert capabilities.position_encoding == "utf-16"


def test_initialize_result_validates_capabilities_at_the_wire_boundary() -> None:
    result = soleaux.lsp.contracts.InitializeResult.from_lsp(
        {
            "capabilities": {"textDocumentSync": 1, "hoverProvider": True},
            "serverInfo": {"name": "fixture-lsp", "version": "2.4.1"},
        }
    )

    assert result.capabilities.text_document_sync is soleaux.lsp.contracts.TextDocumentSyncKind.FULL
    assert result.capabilities.open_close is True
    assert result.capabilities.hover_provider is True
    assert result.server_info is not None
    assert result.server_info.name == "fixture-lsp"
    assert result.server_info.version == "2.4.1"
