"""D029: canonical symbol identity is stable and generation-bound."""

import soleaux.lsp.contracts
import soleaux.lsp.operations


def test_symbol_identity_is_stable_across_equivalent_generations() -> None:
    location = soleaux.lsp.contracts.LspLocation(
        uri="file:///workspace/src/example.py",
        range=soleaux.lsp.contracts.LspRange(
            start=soleaux.lsp.contracts.LspPosition(line=2, character=4),
            end=soleaux.lsp.contracts.LspPosition(line=2, character=10),
        ),
    )

    first = soleaux.lsp.operations.SymbolIdentity.from_location(
        location,
        provider_name="pylsp",
        generation_fingerprint="generation-one",
        name="target",
    )
    second = soleaux.lsp.operations.SymbolIdentity.from_location(
        location,
        provider_name="pylsp",
        generation_fingerprint="generation-two",
        name="target",
    )

    assert first.symbol_id == second.symbol_id
    assert first.generation_fingerprint != second.generation_fingerprint
    assert first.location == location
