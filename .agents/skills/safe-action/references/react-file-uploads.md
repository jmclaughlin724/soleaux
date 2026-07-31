# File Upload Actions

Use this route only when the target already owns `next-safe-action` and the requested action accepts `FormData`. Confirm the installed action API, storage owner, authorization boundary, and request-size limits before implementation.

## Server Contract

1. Authenticate and authorize the target resource independently from input validation.
2. Read the expected `FormData` keys and reject missing, repeated, or unexpected values according to the action contract.
3. Prove each upload is a `File`; enforce count and byte limits before buffering.
4. Validate allowed media using server-observed content when trust matters. A browser MIME type and filename are hints, not proof.
5. Generate storage keys in the owning storage adapter, not from an untrusted filename.
6. Return the narrowest stable DTO and clean up partial writes on failure.

Use the target's existing Zod and form-data adapter if one is installed. Do not add `zod-form-data` from this reference. If no adapter exists, map the expected `FormData` entries explicitly and validate those values with the owning schema.

## Client Contract

- Preserve a real `<form>`, labelled file input, submit button, pending state, and actionable error message.
- Keep accepted file types and size guidance visible, while enforcing the same rules again on the server.
- Prevent duplicate submission and define retry behavior.
- Do not preview untrusted content without safe object-URL lifecycle and the media-specific security review.

## Verification

Test missing files, wrong types, oversized and repeated files, unauthorized targets, storage failure and cleanup, duplicate submission, successful DTO shape, and the installed action hook's pending/error behavior.
