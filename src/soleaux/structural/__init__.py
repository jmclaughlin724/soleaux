"""Structural plane: snapshotter, supervised ast-grep worker, projections, and rules.

The structural plane owns declarations, imports, exports, call-site
candidates, registrations, entrypoint candidates, and syntax policy matches.
It never resolves symbols, references, consumers, or call edges, and it never
returns a live AST handle — only compact serializable rows.
"""
