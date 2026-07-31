---
title: Review and apply editor changes safely
description: Preview, review, and safely apply Soleaux editor changes with hash-bound patches, explicit confirmation, preimage validation, and conflict handling.
sidebar:
  label: Safe edits
  order: 6
---

Soleaux separates editor discovery from mutation. `preview` produces a reviewable, no-write result; `edit` is the only tool that can write that result.

## Preview an edit

Preview operations are `rename`, `format_document`, `format_range`, `code_action`, and `structural_rewrite` (a typed ast-grep matcher with a declared or explicit fix). A successful preview includes sorted, non-overlapping repository-relative patches, a unified diff, affected paths, preimage and postimage hashes, provider generation, process epoch, issue and expiry times, a preview ID, and a digest.

The preview is bound to the selected workspace, provider generation, target, source hashes, and current process; structural previews additionally bind the engine version and the rule digest. It cannot be replayed in a different process or after it expires. A structural preview is never partial: parse failures, unsupported languages, overlapping edits, or budget truncation fail before issuance, and zero matches return `no_changes`.

## Apply the reviewed preview

Pass the exact `preview_id`, `digest`, and `confirm: true` to `edit`. Before writing any file, Soleaux revalidates:

1. the preview exists and is unused;
2. the digest, workspace, process epoch, and provider generation still match;
3. the preview has not expired;
4. every live file matches its recorded preimage hash.

Any preflight conflict aborts before the first write. Apply reports an exact state of `applied`, `rolled_back`, `partial_failure`, or `conflicted`, with one result per affected file.

## Keep host approval enabled

`edit` requires explicit confirmation in its request and revalidates the selected preview; `restart_lsp` acts only on explicitly selected sessions. The other eight local tools are read-only, including `preview`. MCP host approval is configured independently by the client.
