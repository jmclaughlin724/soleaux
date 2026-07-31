# CLI Workflows

Use the repository-pinned CLI through `pnpm exec ast-grep`.

## Inspect Code Structure

Dump the parsed structure when choosing node kinds or debugging a pattern:

```bash
pnpm exec ast-grep run \
  --pattern 'async function example() { await fetch(); }' \
  --lang javascript \
  --debug-query=cst
```

Available formats:

- `cst`: the concrete syntax tree, including punctuation;
- `ast`: named nodes only;
- `pattern`: ast-grep's interpretation of the query pattern.

Inspect both the target and the pattern when a match is surprising:

```bash
pnpm exec ast-grep run \
  --pattern 'class User { constructor() {} }' \
  --lang javascript \
  --debug-query=cst

pnpm exec ast-grep run \
  --pattern 'class $NAME { $$$BODY }' \
  --lang javascript \
  --debug-query=pattern
```

## Select a Command

Use `run` for a simple single-node pattern:

```bash
pnpm exec ast-grep run --pattern 'console.log($ARG)' --lang javascript .
pnpm exec ast-grep run --pattern 'class $NAME' --lang python .
pnpm exec ast-grep run \
  --pattern 'function $NAME($$$)' \
  --lang javascript \
  --json .
```

Use `scan` for YAML rules, relational matching, or composite logic:

```bash
pnpm exec ast-grep scan --rule my_rule.yml .
pnpm exec ast-grep scan --rule my_rule.yml --json .
```

Test an inline rule through standard input:

```bash
echo "const x = await fetch();" |
  pnpm exec ast-grep scan --inline-rules "id: test
language: javascript
rule:
  pattern: await \$EXPR" --stdin
```

When embedding YAML in a shell argument, escape metavariables as `\$VAR` or quote the complete argument so the shell does not expand `$`.

## Debugging Order

1. Start with the smallest `pattern`.
2. Inspect the target with `--debug-query=cst`.
3. Confirm the language's node `kind`.
4. Add `has` or `inside` with an explicit `stopBy`.
5. Add `all`, `any`, or `not` only after each sub-rule matches independently.
6. Run positive and negative examples with the pinned CLI.

Use `stopBy: end` when the relation must traverse the full direction. Choose a nearer boundary only when the rule intentionally limits that traversal.

## Worked Queries

Find async function declarations containing `await`:

```bash
pnpm exec ast-grep scan --inline-rules "id: async-await
language: javascript
rule:
  all:
    - kind: function_declaration
    - has:
        pattern: await \$EXPR
        stopBy: end" .
```

Find `console.log` inside a method:

```bash
pnpm exec ast-grep scan --inline-rules "id: console-in-class
language: javascript
rule:
  pattern: console.log(\$\$\$)
  inside:
    kind: method_definition
    stopBy: end" .
```

Find async function declarations that have no `try` statement:

```bash
pnpm exec ast-grep scan --inline-rules "id: async-no-trycatch
language: javascript
rule:
  all:
    - kind: function_declaration
    - has:
        pattern: await \$EXPR
        stopBy: end
    - not:
        has:
          pattern: try { \$\$\$ } catch (\$E) { \$\$\$ }
          stopBy: end" .
```
