"use strict";

const { createRequire } = require("node:module");
const { isAbsolute, join } = require("node:path");

const PARSER_PACKAGE = "@libpg-query/parser";
const PARSER_VERSION = "17.6.10";
const MAX_FRAME_BYTES = 8_388_608;
const MAX_SOURCE_BYTES = 4_194_304;
const LINE_FEED_BYTE = 10;
const MAX_ERROR_MESSAGE_CHARACTERS = 280;
const SEMICOLON_TOKEN_TYPE = 59;

const [managedPrefix] = process.argv.slice(2);
const workerState = {
  isShuttingDown: false,
  pending: Buffer.alloc(0),
  requests: Promise.resolve(),
};

const parserPromise = initializeParser();

process.stdin.on("data", (chunk) => {
  if (workerState.isShuttingDown) {
    return;
  }
  workerState.pending = Buffer.concat([workerState.pending, chunk]);
  if (
    workerState.pending.length > MAX_FRAME_BYTES &&
    !workerState.pending.includes(LINE_FEED_BYTE)
  ) {
    writeResponse({
      id: null,
      status: "error",
      error: {
        type: "frame_too_large",
        message: "request frame exceeds the 8 MiB cap",
      },
    });
    workerState.isShuttingDown = true;
    process.stdin.destroy();
    return;
  }
  let newline = workerState.pending.indexOf(LINE_FEED_BYTE);
  while (newline !== -1) {
    const frame = workerState.pending.subarray(0, newline);
    workerState.pending = workerState.pending.subarray(newline + 1);
    if (frame.length > MAX_FRAME_BYTES) {
      writeResponse({
        id: null,
        status: "error",
        error: {
          type: "frame_too_large",
          message: "request frame exceeds the 8 MiB cap",
        },
      });
      workerState.isShuttingDown = true;
      process.stdin.destroy();
      return;
    }
    workerState.requests = enqueueFrame(workerState.requests, frame);
    newline = workerState.pending.indexOf(LINE_FEED_BYTE);
  }
});

process.stdin.on("end", async () => {
  try {
    await workerState.requests;
  } finally {
    process.exitCode = 0;
  }
});

async function initializeParser() {
  if (!managedPrefix || !isAbsolute(managedPrefix)) {
    throw new Error("managed parser prefix must be absolute");
  }
  const managedRequire = createRequire(join(managedPrefix, "package.json"));
  const packageJson = managedRequire(`${PARSER_PACKAGE}/package.json`);
  if (
    packageJson.name !== PARSER_PACKAGE ||
    packageJson.version !== PARSER_VERSION
  ) {
    throw new Error(
      `managed parser identity is ${packageJson.name}@${packageJson.version}, ` +
        `expected ${PARSER_PACKAGE}@${PARSER_VERSION}`
    );
  }
  const parser = managedRequire(PARSER_PACKAGE);
  await parser.loadModule();
  return parser;
}

async function enqueueFrame(previousRequest, frame) {
  await previousRequest;
  await handleFrame(frame);
}

async function handleFrame(frame) {
  const request = decodeRequest(frame);
  if (request === null) {
    return;
  }
  const id = request.id ?? null;
  const parser = await availableParser(id);
  if (parser === null || handleControlRequest(request, id)) {
    return;
  }
  if (request.op !== "analyze") {
    writeResponse({
      id,
      status: "error",
      error: { type: "unknown_op", message: String(request.op) },
    });
    return;
  }
  if (typeof request.source !== "string") {
    writeResponse({
      id,
      status: "error",
      error: { type: "bad_source", message: "source must be a string" },
    });
    return;
  }
  if (Buffer.byteLength(request.source, "utf-8") > MAX_SOURCE_BYTES) {
    writeResponse({
      id,
      status: "error",
      error: {
        type: "source_too_large",
        message: "source exceeds the 4 MiB cap",
      },
    });
    return;
  }
  const scan = scanSource(parser, request.source, id);
  if (scan === null) {
    return;
  }
  analyzeSource(parser, request.source, scan, id);
}

function decodeRequest(frame) {
  try {
    const request = JSON.parse(frame.toString("utf-8"));
    if (request && typeof request === "object" && !Array.isArray(request)) {
      return request;
    }
  } catch {
    // The stable bad-frame response below covers syntax and shape failures.
  }
  writeResponse({
    id: null,
    status: "error",
    error: { type: "bad_frame", message: "request is not valid JSON object" },
  });
  return null;
}

async function availableParser(id) {
  try {
    return await parserPromise;
  } catch (error) {
    writeResponse({
      id,
      status: "error",
      error: { type: "parser_unavailable", message: errorMessage(error) },
    });
    return null;
  }
}

function handleControlRequest(request, id) {
  if (request.op === "ping") {
    writeResponse({
      id,
      status: "ok",
      op: "pong",
      parser: { package: PARSER_PACKAGE, version: PARSER_VERSION },
    });
    return true;
  }
  if (request.op === "shutdown") {
    workerState.isShuttingDown = true;
    writeResponse(
      {
        id,
        status: "ok",
        op: "shutdown",
        parser: { package: PARSER_PACKAGE, version: PARSER_VERSION },
      },
      true
    );
    return true;
  }
  return false;
}

function scanSource(parser, source, id) {
  try {
    return parser.scanSync(source);
  } catch (error) {
    writeResponse({
      id,
      status: "error",
      error: {
        type: "parser_failure",
        message: errorMessage(error),
      },
    });
    return null;
  }
}

function analyzeSource(parser, source, scan, id) {
  try {
    const parseTree = parser.parseSync(source);
    const embeddedQueries = extractEmbeddedQueries(parser, source, parseTree);
    writeResponse({
      id,
      status: "ok",
      parser: { package: PARSER_PACKAGE, version: PARSER_VERSION },
      offset_unit: "utf8_byte",
      error_cursor_unit: "unicode_code_point",
      parse_tree: parseTree,
      scan,
      recovered: false,
      parse_errors: [],
      embedded_queries: embeddedQueries.queries,
      plpgsql_error: embeddedQueries.error,
    });
  } catch (error) {
    if (!(error instanceof parser.SqlError)) {
      writeResponse({
        id,
        status: "error",
        error: {
          type: "parser_failure",
          message: errorMessage(error),
        },
      });
      return;
    }
    const recovered = recoverStatements(parser, source, scan);
    if (recovered.statements.length === 0) {
      const sqlDetails =
        typeof parser.hasSqlDetails === "function" &&
        parser.hasSqlDetails(error)
          ? error.sqlDetails
          : undefined;
      const cursor =
        sqlDetails && Number.isSafeInteger(sqlDetails.cursorPosition)
          ? sqlDetails.cursorPosition
          : null;
      writeResponse({
        id,
        status: "error",
        error: {
          type: "parse_error",
          message: errorMessage(error),
          cursor_position: cursor,
          cursor_unit: "unicode_code_point",
        },
      });
      return;
    }
    writeResponse({
      id,
      status: "ok",
      parser: { package: PARSER_PACKAGE, version: PARSER_VERSION },
      offset_unit: "utf8_byte",
      error_cursor_unit: "unicode_code_point",
      parse_tree: {
        version: scan.version,
        stmts: recovered.statements,
      },
      scan,
      recovered: true,
      parse_errors: recovered.errors,
      embedded_queries: [],
      plpgsql_error: null,
    });
  }
}

function scannerRanges(tokens) {
  const ranges = [];
  let start = null;
  let lastEnd = null;
  for (const token of tokens) {
    if (token.tokenType === SEMICOLON_TOKEN_TYPE) {
      if (start !== null) {
        ranges.push([start, token.end]);
        start = null;
        lastEnd = null;
      }
      continue;
    }
    if (start === null) {
      ({ start } = token);
    }
    lastEnd = token.end;
  }
  if (start !== null && lastEnd !== null) {
    ranges.push([start, lastEnd]);
  }
  return ranges;
}

function recoverStatements(parser, source, scan) {
  const sourceBytes = Buffer.from(source, "utf-8");
  const statements = [];
  const errors = [];
  for (const [start, end] of scannerRanges(scan.tokens)) {
    const statementSource = sourceBytes.subarray(start, end).toString("utf-8");
    try {
      const document = parser.parseSync(statementSource);
      for (const statement of document.stmts) {
        statements.push(shiftLocations(statement, start));
      }
    } catch (error) {
      const sqlDetails =
        typeof parser.hasSqlDetails === "function" &&
        parser.hasSqlDetails(error)
          ? error.sqlDetails
          : undefined;
      const cursor =
        sqlDetails && Number.isSafeInteger(sqlDetails.cursorPosition)
          ? sqlDetails.cursorPosition
          : 0;
      const byteStart =
        start +
        Buffer.byteLength(
          [...statementSource].slice(0, cursor).join(""),
          "utf-8"
        );
      statements.push({
        stmt: {
          ERROR: {
            message: errorMessage(error),
            location: byteStart,
          },
        },
        stmt_location: start,
        stmt_len: end - start,
      });
      errors.push({
        message: errorMessage(error),
        byte_start: byteStart,
        byte_end: end,
      });
    }
  }
  return { statements, errors };
}

function shiftLocations(value, byteDelta, key = "") {
  if (Array.isArray(value)) {
    return value.map((item) => shiftLocations(item, byteDelta));
  }
  if (!value || typeof value !== "object") {
    if (
      (key === "location" || key === "stmt_location") &&
      Number.isSafeInteger(value) &&
      value >= 0
    ) {
      return value + byteDelta;
    }
    return value;
  }
  const shifted = {};
  for (const [name, item] of Object.entries(value)) {
    shifted[name] = shiftLocations(item, byteDelta, name);
  }
  return shifted;
}

function extractEmbeddedQueries(parser, source, parseTree) {
  let tree;
  try {
    tree = parser.parsePlPgSQLSync(source);
  } catch (error) {
    return { queries: [], error: errorMessage(error) };
  }
  const queries = [];
  const functions =
    tree && Array.isArray(tree.plpgsql_funcs) ? tree.plpgsql_funcs : [];
  const contexts = plpgsqlContexts(source, parseTree);
  if (functions.length !== contexts.length) {
    return {
      queries: [],
      error: "PL/pgSQL block/source context count mismatch",
    };
  }
  for (const [index, function_] of functions.entries()) {
    extractFunctionQueries(parser, function_, contexts[index], queries);
  }
  return { queries, error: null };
}

function plpgsqlContexts(source, parseTree) {
  const sourceBytes = Buffer.from(source, "utf-8");
  const statements =
    parseTree && Array.isArray(parseTree.stmts) ? parseTree.stmts : [];
  const contexts = [];
  for (const statement of statements) {
    const context = plpgsqlStatementContext(sourceBytes, statement);
    if (context !== null) {
      contexts.push(context);
    }
  }
  return contexts;
}

function plpgsqlStatementContext(sourceBytes, statement) {
  const payload = plpgsqlStatementPayload(statement);
  if (payload === null) {
    return null;
  }
  const statementStart = Number.isSafeInteger(statement.stmt_location)
    ? statement.stmt_location
    : 0;
  const body = plpgsqlBody(payload);
  if (body === null) {
    return lineAtByte(sourceBytes, statementStart);
  }
  const bodyStart = sourceBytes.indexOf(
    Buffer.from(body, "utf-8"),
    statementStart
  );
  return lineAtByte(sourceBytes, Math.max(bodyStart, statementStart));
}

function plpgsqlStatementPayload(statement) {
  if (!statement || typeof statement !== "object") {
    return null;
  }
  const statementNode = statement.stmt;
  if (!statementNode || typeof statementNode !== "object") {
    return null;
  }
  const functionPayload = statementNode.CreateFunctionStmt;
  if (functionPayload && typeof functionPayload === "object") {
    return functionPayload;
  }
  const doPayload = statementNode.DoStmt;
  return doPayload && typeof doPayload === "object" ? doPayload : null;
}

function plpgsqlBody(payload) {
  let options = [];
  if (Array.isArray(payload.options)) {
    ({ options } = payload);
  } else if (Array.isArray(payload.args)) {
    ({ args: options } = payload);
  }
  for (const option of options) {
    const body = plpgsqlBodyOption(option);
    if (body !== null) {
      return body;
    }
  }
  return null;
}

function plpgsqlBodyOption(option) {
  if (!option || typeof option.DefElem !== "object") {
    return null;
  }
  const definition = option.DefElem;
  if (definition.defname !== "as" || typeof definition.arg !== "object") {
    return null;
  }
  if (definition.arg.String && typeof definition.arg.String.sval === "string") {
    return definition.arg.String.sval;
  }
  const list = definition.arg.List;
  if (!list || !Array.isArray(list.items)) {
    return null;
  }
  const [firstItem] = list.items;
  if (!firstItem || typeof firstItem.String !== "object") {
    return null;
  }
  return typeof firstItem.String.sval === "string"
    ? firstItem.String.sval
    : null;
}

function lineAtByte(sourceBytes, byteOffset) {
  let line = 0;
  for (let index = 0; index < byteOffset; index += 1) {
    if (sourceBytes[index] === LINE_FEED_BYTE) {
      line += 1;
    }
  }
  return line;
}

function extractFunctionQueries(parser, tree, baseLine, queries) {
  const stack = [{ value: tree, line: baseLine, dynamic: false }];
  while (stack.length > 0) {
    const current = stack.pop();
    const { value } = current;
    if (Array.isArray(value)) {
      pushArrayValues(stack, value, current);
      continue;
    }
    if (!value || typeof value !== "object") {
      continue;
    }
    const context = plpgsqlContext(value, current, baseLine);
    const query = embeddedQuery(parser, value, context);
    if (query !== null) {
      queries.push(query);
      continue;
    }
    for (const child of Object.values(value)) {
      stack.push({ value: child, ...context });
    }
  }
}

function pushArrayValues(stack, values, context) {
  for (const value of values.toReversed()) {
    stack.push({
      value,
      line: context.line,
      dynamic: context.dynamic,
    });
  }
}

function plpgsqlContext(value, parent, baseLine) {
  const line =
    Number.isSafeInteger(value.lineno) && value.lineno > 0
      ? baseLine + value.lineno - 1
      : parent.line;
  return {
    line,
    dynamic:
      parent.dynamic ||
      Object.hasOwn(value, "PLpgSQL_stmt_dynexecute") ||
      Object.hasOwn(value, "PLpgSQL_stmt_dynfors"),
  };
}

function embeddedQuery(parser, value, context) {
  if (
    !Object.hasOwn(value, "query") ||
    typeof value.query !== "string" ||
    !Object.hasOwn(value, "parseMode")
  ) {
    return null;
  }
  if (context.dynamic) {
    return { line: context.line, dynamic: true, parse_tree: null };
  }
  try {
    return {
      line: context.line,
      dynamic: false,
      parse_tree: parser.parseSync(value.query),
    };
  } catch {
    return { line: context.line, dynamic: false, parse_tree: null };
  }
}

function writeResponse(payload, shouldExitAfterWrite = false) {
  let frame = Buffer.from(`${JSON.stringify(payload)}\n`, "utf-8");
  if (frame.length > MAX_FRAME_BYTES) {
    frame = Buffer.from(
      `${JSON.stringify({
        id: payload.id ?? null,
        status: "error",
        error: {
          type: "response_too_large",
          message: "response exceeds the 8 MiB cap",
        },
      })}\n`,
      "utf-8"
    );
  }
  process.stdout.write(frame, () => {
    if (shouldExitAfterWrite) {
      process.exit(0);
    }
  });
}

function errorMessage(error) {
  return Error.isError(error)
    ? error.message.slice(0, MAX_ERROR_MESSAGE_CHARACTERS)
    : String(error).slice(0, MAX_ERROR_MESSAGE_CHARACTERS);
}
