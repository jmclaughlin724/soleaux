import { fileURLToPath } from "node:url";

import { expect, test } from "vitest";

import { parseBash } from "../PreToolUse/bash-ast.mjs";
import { evaluateRawSqlDdlGuard } from "../PreToolUse/raw-sql-ddl-guard.mjs";

const repoRoot = fileURLToPath(new URL("../../..", import.meta.url));

function bash(command) {
  return {
    bash: parseBash(command),
    cwd: repoRoot,
  };
}

test.each([
  ['psql -c "CREATE TABLE foo (id int)"', "CREATE TABLE"],
  ['supabase db execute --sql "DROP TABLE users"', "DROP OBJECT"],
  ['psql --command "ALTER TABLE foo ADD COLUMN bar int"', "ALTER TABLE"],
  ['psql -c "GRANT SELECT ON foo TO public"', "GRANT"],
  ['psql -c "REVOKE SELECT ON foo FROM public"', "REVOKE"],
  ['psql -c "TRUNCATE TABLE foo"', "TRUNCATE"],
  [
    "psql <<'SQL'\nCREATE POLICY owner_only ON documents USING (true);\nSQL",
    "CREATE POLICY",
  ],
  [
    "bash -lc 'psql -c \"CREATE INDEX foo_id_idx ON foo (id)\"'",
    "CREATE INDEX",
  ],
])("blocks parsed SQL DDL through Bash", async (command, label) => {
  const reason = await evaluateRawSqlDdlGuard(bash(command));
  expect(reason).toContain(label);
});

test.each([
  'psql -c "SELECT create_table FROM audit_log"',
  "psql -c \"SELECT 'DROP TABLE users'\"",
  'supabase db query --sql "SELECT 1"',
  "echo 'CREATE TABLE ignored(id int)'",
])("remains silent for parsed non-DDL SQL: %s", async (command) => {
  await expect(evaluateRawSqlDdlGuard(bash(command))).resolves.toBeNull();
});

test("reports a parser failure when relevant SQL cannot be parsed", async () => {
  await expect(
    evaluateRawSqlDdlGuard(bash('psql -c "CREATE TABLE"'))
  ).rejects.toThrow();
});
