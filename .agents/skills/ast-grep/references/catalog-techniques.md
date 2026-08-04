# Catalog Techniques

Use these upstream catalog entries as technique references when creating or updating rules:

- [Avoid Unnecessary React Hook](https://astgrep.com/catalog/tsx/unnecessary-react-hook): reusable `utils`, `matches`, composite rules, descendant searches, and metavariable constraints
- [Speed up Barrel Import](https://astgrep.com/catalog/typescript/speed-up-barrel-import): multi-node captures, local rewriters, `transform.rewrite`, and `joinBy`
- [Use Logical Assignment](https://astgrep.com/catalog/typescript/use-logical-assignment): a minimal repeated-metavariable match and fix
- [Rewrite SQLAlchemy Mapped Column](https://astgrep.com/catalog/python/rewrite-sqlalchemy-mapped-column): filtering captured arguments with a rewriter while adding a type annotation
- [Remove Async/Await](https://astgrep.com/catalog/python/remove-async-await): nested rewriting for overlapping function and call changes
- [Refactor pytest Fixtures](https://astgrep.com/catalog/python/refactor-pytest-fixtures): reusable relational utilities for fixture declarations and their contextual uses
- [Recursive Rewrite Type](https://astgrep.com/catalog/python/recursive-rewrite-type): composing recursive rewriters for nested syntax
- [No Await in Promise.all](https://astgrep.com/catalog/typescript/no-await-in-promise-all): bounded relational matching with an explicit `stopBy`
- [Find Import Usage](https://astgrep.com/catalog/typescript/find-import-usage): repeated metavariable binding across an import and its containing program
- [Find Import Identifiers](https://astgrep.com/catalog/typescript/find-import-identifiers): field-aware extraction across ES module and CommonJS import forms

Treat catalog examples as prototypes rather than repository-ready rules. Some are one-off searches or codemods, not diagnostics. Before promoting one into `tools/ast-grep/rules`, check whether the compiler, Biome, Ultracite, Ruff, or another canonical owner already enforces the behavior. Test the adapted rule with the repository-pinned CLI, add representative positive and negative cases, and satisfy the owning rule library's metadata, scope, fixture, and snapshot requirements.

Account for known limitations in the published examples:

- The React Hook example's `^use` regex also matches ordinary names such as `userPathEnd`, `userSpecificAbsolutePaths`, and `usesSecurityInvoker`, while its direct-call check misses qualified hooks such as `React.useEffect`. Soleaux does not ship that application-specific policy; any consuming-repository adaptation must avoid prefix-only matching and recognize both direct and qualified Hook calls.
- The barrel-import rewrite assumes every named import maps to a default export at a same-named subpath; verify the package export topology before applying it.
- The Remove Async/Await page expresses `rewriters` as a mapping, which the repository-pinned ast-grep 0.45.0 rejects because it requires a sequence. Correcting the shape makes the YAML parse, but does not make the semantic refactor automatically safe.
- The pytest fixture page's rename example references an undefined `is-fixture-context` utility; supply and test the intended context rule before adapting it.
- The import examples are syntactic queries, not symbol resolution. In particular, the published Find Import Usage rule also returns the import binding itself and cannot distinguish shadowed identifiers.
