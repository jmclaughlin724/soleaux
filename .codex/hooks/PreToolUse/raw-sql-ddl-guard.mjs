/* eslint-disable no-magic-numbers -- Database CLI token positions are protocol mechanics. */
import { parse } from "@libpg-query/parser";

import { commandArguments, commandName } from "./bash-ast.mjs";

const DDL_NODE_LABELS = new Map([
  ["AlterCollationStmt", "ALTER COLLATION"],
  ["AlterDatabaseStmt", "ALTER DATABASE"],
  ["AlterDatabaseSetStmt", "ALTER DATABASE"],
  ["AlterDefaultPrivilegesStmt", "ALTER DEFAULT PRIVILEGES"],
  ["AlterDomainStmt", "ALTER DOMAIN"],
  ["AlterEnumStmt", "ALTER TYPE"],
  ["AlterEventTrigStmt", "ALTER EVENT TRIGGER"],
  ["AlterExtensionContentsStmt", "ALTER EXTENSION"],
  ["AlterExtensionStmt", "ALTER EXTENSION"],
  ["AlterFdwStmt", "ALTER FOREIGN DATA WRAPPER"],
  ["AlterForeignServerStmt", "ALTER SERVER"],
  ["AlterFunctionStmt", "ALTER FUNCTION"],
  ["AlterObjectDependsStmt", "ALTER OBJECT"],
  ["AlterObjectSchemaStmt", "ALTER OBJECT SCHEMA"],
  ["AlterOpFamilyStmt", "ALTER OPERATOR FAMILY"],
  ["AlterOperatorStmt", "ALTER OPERATOR"],
  ["AlterOwnerStmt", "ALTER OWNER"],
  ["AlterPolicyStmt", "ALTER POLICY"],
  ["AlterPublicationStmt", "ALTER PUBLICATION"],
  ["AlterRoleSetStmt", "ALTER ROLE"],
  ["AlterRoleStmt", "ALTER ROLE"],
  ["AlterSeqStmt", "ALTER SEQUENCE"],
  ["AlterStatsStmt", "ALTER STATISTICS"],
  ["AlterSubscriptionStmt", "ALTER SUBSCRIPTION"],
  ["AlterSystemStmt", "ALTER SYSTEM"],
  ["AlterTableMoveAllStmt", "ALTER TABLE"],
  ["AlterTableSpaceOptionsStmt", "ALTER TABLESPACE"],
  ["AlterTableStmt", "ALTER TABLE"],
  ["AlterTSConfigurationStmt", "ALTER TEXT SEARCH CONFIGURATION"],
  ["AlterTSDictionaryStmt", "ALTER TEXT SEARCH DICTIONARY"],
  ["AlterTypeStmt", "ALTER TYPE"],
  ["CompositeTypeStmt", "CREATE TYPE"],
  ["CreateAmStmt", "CREATE ACCESS METHOD"],
  ["CreateCastStmt", "CREATE CAST"],
  ["CreateConversionStmt", "CREATE CONVERSION"],
  ["CreateDomainStmt", "CREATE DOMAIN"],
  ["CreateEnumStmt", "CREATE TYPE"],
  ["CreateEventTrigStmt", "CREATE EVENT TRIGGER"],
  ["CreateExtensionStmt", "CREATE EXTENSION"],
  ["CreateFdwStmt", "CREATE FOREIGN DATA WRAPPER"],
  ["CreateForeignServerStmt", "CREATE SERVER"],
  ["CreateForeignTableStmt", "CREATE FOREIGN TABLE"],
  ["CreateFunctionStmt", "CREATE FUNCTION"],
  ["CreateOpClassStmt", "CREATE OPERATOR CLASS"],
  ["CreateOpFamilyStmt", "CREATE OPERATOR FAMILY"],
  ["CreatePLangStmt", "CREATE LANGUAGE"],
  ["CreatePolicyStmt", "CREATE POLICY"],
  ["CreatePublicationStmt", "CREATE PUBLICATION"],
  ["CreateRangeStmt", "CREATE TYPE"],
  ["CreateRoleStmt", "CREATE ROLE"],
  ["CreateSchemaStmt", "CREATE SCHEMA"],
  ["CreateSeqStmt", "CREATE SEQUENCE"],
  ["CreateStatsStmt", "CREATE STATISTICS"],
  ["CreateStmt", "CREATE TABLE"],
  ["CreateSubscriptionStmt", "CREATE SUBSCRIPTION"],
  ["CreateTableAsStmt", "CREATE TABLE AS"],
  ["CreateTableSpaceStmt", "CREATE TABLESPACE"],
  ["CreateTransformStmt", "CREATE TRANSFORM"],
  ["CreateTrigStmt", "CREATE TRIGGER"],
  ["CreateUserMappingStmt", "CREATE USER MAPPING"],
  ["DefineStmt", "CREATE OBJECT"],
  ["DropOwnedStmt", "DROP OWNED"],
  ["DropRoleStmt", "DROP ROLE"],
  ["DropStmt", "DROP OBJECT"],
  ["GrantRoleStmt", "GRANT OR REVOKE ROLE"],
  ["IndexStmt", "CREATE INDEX"],
  ["RenameStmt", "RENAME OBJECT"],
  ["RuleStmt", "CREATE RULE"],
  ["SecLabelStmt", "SECURITY LABEL"],
  ["TruncateStmt", "TRUNCATE"],
  ["ViewStmt", "CREATE VIEW"],
]);

function optionValue(arguments_, names) {
  for (let index = 0; index < arguments_.length; index += 1) {
    const argument = arguments_[index] ?? "";
    const separator = argument.indexOf("=");
    const name = separator === -1 ? argument : argument.slice(0, separator);
    if (!names.has(name)) {
      continue;
    }
    return separator === -1
      ? (arguments_[index + 1] ?? "")
      : argument.slice(separator + 1);
  }
  return "";
}

function sqlCandidate(words) {
  const name = commandName(words);
  const arguments_ = commandArguments(words);
  if (name === "psql") {
    return optionValue(arguments_, new Set(["--command", "-c"]));
  }
  if (
    name === "supabase" &&
    arguments_[0] === "db" &&
    ["execute", "query"].includes(arguments_[1] ?? "")
  ) {
    return optionValue(arguments_.slice(2), new Set(["--sql"]));
  }
  return "";
}

function statementLabel(statement) {
  const nodeName = Object.keys(statement)[0] ?? "";
  if (nodeName === "GrantStmt") {
    return statement.GrantStmt?.is_grant ? "GRANT" : "REVOKE";
  }
  return DDL_NODE_LABELS.get(nodeName) ?? "";
}

async function parsedDdlLabels(sql) {
  const tree = await parse(sql);
  return tree.stmts.map(({ stmt }) => statementLabel(stmt)).filter(Boolean);
}

export async function evaluateRawSqlDdlGuard({ bash }) {
  const candidates = [];
  let hasPsqlInvocation = false;
  for (const { words } of bash.invocations) {
    hasPsqlInvocation ||= commandName(words) === "psql";
    const sql = sqlCandidate(words);
    if (sql) {
      candidates.push(sql);
    }
  }
  if (hasPsqlInvocation) {
    candidates.push(...bash.heredocs);
  }
  const parsedLabels = await Promise.all(candidates.map(parsedDdlLabels));
  const labels = parsedLabels.flat();
  if (labels.length > 0) {
    const uniqueLabels = [...new Set(labels)].join(", ");
    return [
      `BLOCKED: raw SQL DDL through Bash detected: ${uniqueLabels}.`,
      "Structural database changes must use the declarative schema and generated migration workflow.",
      "Corrective action: Edit the desired state under `supabase/schemas/**`, run `pnpm db:schema:check`, then follow the generation command and schema list owned by `supabase/AGENTS.md` and `supabase/scripts/database-schemas.mjs`; validate the changed migration with `pnpm exec supaschema check --changed`.",
    ].join("\n");
  }
  return null;
}
