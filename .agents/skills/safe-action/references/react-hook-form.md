# React Hook Form Adapter

Use the adapter only when the target already owns React Hook Form, `@hookform/resolvers`, and the version-compatible next-safe-action adapter. The native form or existing action hook is the smaller default for a new flow.

## Version Gate

Before copying an API shape, inspect the installed declarations for:

- the adapter package and import paths;
- the action-hook arguments and returned state;
- validation-error mapping and root-error behavior;
- reset semantics; and
- optimistic-action support.

Do not add these dependencies from this reference. If the capability is explicitly requested but absent, treat dependency introduction as its own authorized package decision.

## Integration Contract

1. Keep one schema owner shared by the form resolver and server action when the same input contract crosses both boundaries.
2. Treat client validation as feedback only; the action still validates and authorizes on the server.
3. Map server field errors to the corresponding fields and preserve a separate root error for form-wide failures.
4. Drive pending and disabled UI from the installed action state.
5. Reset form and action state only after the accepted success transition.
6. Preserve submitted values on expected errors unless the product contract deliberately clears them.

## Verification

Test client validation, server validation, root and field errors, duplicate submit prevention, success reset, expected server failure, optimistic rollback when used, focus movement to the first invalid field, and accessible error announcements.
