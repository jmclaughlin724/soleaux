/* eslint-disable unicorn/filename-case -- Packaged soleaux worker resources use snake_case filenames, matching node_worker.cjs. */
/* eslint-disable no-magic-numbers -- Frame caps, exit codes, and offset arithmetic are wire-protocol mechanics. */
/* eslint-disable no-inline-comments -- Lazy import chunk annotations are required by the configured import rule. */

import { readFileSync, realpathSync } from "node:fs";
import { isAbsolute, join, relative } from "node:path";
import { pathToFileURL } from "node:url";

const ENGINE_NAME = "napi";
const ENGINE_PACKAGE = "@ast-grep/napi";
const CAPABILITIES = Object.freeze(["soleaux.structural/v1"]);
const MAX_FRAME_BYTES = 8_388_608;
const LINE_FEED_BYTE = 10;
const MAX_ERROR_MESSAGE_CHARACTERS = 280;
const INLINE_PARSE_CONCURRENCY = 4;
const MAX_CAPTURES_PER_FINDING = 16;
const MULTI_MATCH_DOLLAR_COUNT = 3;
const PROTOCOL_ERROR_EXIT_CODE = 2;
const STARTUP_FAILURE_EXIT_CODE = 1;
const METAVARIABLE_CHARACTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_";
const PACKAGE_NAME_CHARACTERS =
  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.";
const WORD_SEPARATOR_CHARACTERS = "-_ /.";
const DEFAULT_LIMITS = Object.freeze({
  max_capture_chars: 200,
  max_findings: 1000,
  max_preview_chars: 200,
});

function requestFault(faultType, message) {
  const fault = new Error(message);
  fault.faultType = faultType;
  return fault;
}

function errorMessage(error) {
  return Error.isError(error)
    ? error.message.slice(0, MAX_ERROR_MESSAGE_CHARACTERS)
    : String(error).slice(0, MAX_ERROR_MESSAGE_CHARACTERS);
}

function faultPayload(error) {
  if (Error.isError(error) && typeof error.faultType === "string") {
    return { message: errorMessage(error), type: error.faultType };
  }
  return { message: errorMessage(error), type: "worker_failure" };
}

function isPackageSegment(value) {
  if (typeof value !== "string" || value.length === 0) {
    return false;
  }
  if (value === "." || value === ".." || value.startsWith(".")) {
    return false;
  }
  return [...value].every((character) =>
    PACKAGE_NAME_CHARACTERS.includes(character)
  );
}

function isPackageName(value) {
  if (typeof value !== "string" || value.length === 0) {
    return false;
  }
  const parts = value.split("/");
  if (!value.startsWith("@")) {
    return parts.length === 1 && isPackageSegment(parts[0]);
  }
  if (parts.length !== 2 || !isPackageSegment(parts[0].slice(1))) {
    return false;
  }
  return isPackageSegment(parts[1]);
}

function isWorkerConfiguration(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  if (
    "resolve_root" in value &&
    (typeof value.resolve_root !== "string" || !isAbsolute(value.resolve_root))
  ) {
    return false;
  }
  return Object.keys(value).every(
    (key) => key === "languages" || key === "resolve_root"
  );
}

function isLanguageConfiguration(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const hasOnlyKnownKeys = Object.keys(value).every(
    (key) => key === "name" || key === "package"
  );
  if (!hasOnlyKnownKeys || typeof value.name !== "string") {
    return false;
  }
  return isPackageName(value.package);
}

function loadConfiguration(rawConfiguration) {
  if (typeof rawConfiguration !== "string" || rawConfiguration.length === 0) {
    throw new Error("worker requires one JSON configuration argument");
  }
  const configuration = JSON.parse(rawConfiguration);
  if (!isWorkerConfiguration(configuration)) {
    throw new Error(
      "worker configuration accepts only package-owned languages"
    );
  }
  if (!Array.isArray(configuration.languages)) {
    throw new TypeError("configuration.languages must be an array");
  }
  for (const language of configuration.languages) {
    if (!isLanguageConfiguration(language)) {
      throw new TypeError(
        "each configured language needs a name and bare package identity"
      );
    }
  }
  return configuration;
}

// Engine and language packages resolve from the soleaux runtime environment
// (the root passed by the launcher), never from the analyzed root or the
// worker's own location: the worker may be consumed through a link whose
// realpath has no node_modules.
function createSpecifierResolver(resolveRoot) {
  const parentUrl = pathToFileURL(join(resolveRoot, "package.json")).href;
  return (specifier) => import.meta.resolve(specifier, parentUrl);
}

function readPackageManifest(packageName, resolveSpecifier) {
  return JSON.parse(
    readFileSync(
      new URL(resolveSpecifier(`${packageName}/package.json`)),
      "utf-8"
    )
  );
}

async function importPackagedModule(packageName, resolveSpecifier) {
  if (!isPackageName(packageName)) {
    throw new Error(`invalid package identity ${String(packageName)}`);
  }
  const namespace = await import(
    /* webpackChunkName: "soleaux-napi-package" */ resolveSpecifier(packageName)
  );
  return namespace.default ?? namespace;
}

async function initializeRuntime(configuration) {
  const resolveSpecifier =
    typeof configuration.resolve_root === "string"
      ? createSpecifierResolver(configuration.resolve_root)
      : (specifier) => import.meta.resolve(specifier);
  const manifest = readPackageManifest(ENGINE_PACKAGE, resolveSpecifier);
  const engine = await importPackagedModule(ENGINE_PACKAGE, resolveSpecifier);
  if (typeof manifest.version !== "string" || manifest.version.length === 0) {
    throw new Error(`${ENGINE_PACKAGE} version is unreadable`);
  }
  if (configuration.languages.length > 0) {
    const loaded = await Promise.all(
      configuration.languages.map((language) =>
        importPackagedModule(language.package, resolveSpecifier)
      )
    );
    const registrations = {};
    for (const [position, language] of configuration.languages.entries()) {
      registrations[language.name] = loaded[position];
    }
    engine.registerDynamicLanguage(registrations);
  }
  const nativeLanguages = Object.getOwnPropertyNames(engine.Lang).filter(
    (name) => typeof engine.Lang[name] === "string"
  );
  const supportedLanguages = new Set([
    ...nativeLanguages,
    ...configuration.languages.map((language) => language.name),
  ]);
  return { engine, engineVersion: manifest.version, supportedLanguages };
}

function resolveLanguage(runtime, language) {
  if (
    typeof language !== "string" ||
    !runtime.supportedLanguages.has(language)
  ) {
    throw requestFault(
      "unsupported_language",
      `language ${String(language)} is not registered with the napi engine`
    );
  }
  return language;
}

function scanMetavariableNames(text, names, seen) {
  let index = 0;
  while (index < text.length) {
    if (text[index] === "$") {
      let cursor = index;
      while (cursor < text.length && text[cursor] === "$") {
        cursor += 1;
      }
      let end = cursor;
      while (end < text.length && METAVARIABLE_CHARACTERS.includes(text[end])) {
        end += 1;
      }
      if (end > cursor) {
        const name = text.slice(cursor, end);
        if (!seen.has(name)) {
          seen.add(name);
          names.push(name);
        }
      }
      index = Math.max(end, cursor);
    } else {
      index += 1;
    }
  }
}

function buildMatcherPlan(matcher) {
  if (matcher?.kind === "pattern" && typeof matcher.pattern === "string") {
    return {
      constraintNames: [],
      napiMatcher: { rule: { pattern: matcher.pattern } },
      scanText: matcher.pattern,
    };
  }
  if (
    matcher?.kind === "rule" &&
    matcher.rule !== null &&
    typeof matcher.rule === "object"
  ) {
    const constraints =
      matcher.constraints !== null && typeof matcher.constraints === "object"
        ? matcher.constraints
        : {};
    const utilities =
      matcher.utils !== null && typeof matcher.utils === "object"
        ? matcher.utils
        : {};
    return {
      constraintNames: Object.keys(constraints),
      napiMatcher: { constraints, rule: matcher.rule, utils: utilities },
      scanText: JSON.stringify({
        constraints,
        rule: matcher.rule,
        utils: utilities,
      }),
    };
  }
  throw requestFault(
    "bad_matcher",
    "matcher must be an inline pattern or an inline rule"
  );
}

function validateMatcher(runtime, language, napiMatcher) {
  try {
    runtime.engine.parse(language, "").root().findAll(napiMatcher);
  } catch (error) {
    throw requestFault("bad_matcher", errorMessage(error));
  }
}

function normalizeLimits(limits) {
  const source = limits !== null && typeof limits === "object" ? limits : {};
  const normalized = { ...DEFAULT_LIMITS };
  for (const key of Object.keys(DEFAULT_LIMITS)) {
    const value = source[key];
    if (Number.isSafeInteger(value) && value > 0) {
      normalized[key] = value;
    }
  }
  return normalized;
}

function truncateCodePoints(text, maximumCharacters) {
  if (text.length <= maximumCharacters) {
    return text;
  }
  const characters = [...text];
  if (characters.length <= maximumCharacters) {
    return text;
  }
  return characters.slice(0, maximumCharacters).join("");
}

function utf8Offset(source, utf16Index) {
  return Buffer.byteLength(source.slice(0, utf16Index), "utf-8");
}

function stripMetavariableSigil(reference) {
  let start = 0;
  while (start < reference.length && reference[start] === "$") {
    start += 1;
  }
  return reference.slice(start);
}

function isUppercaseLetter(character) {
  return character >= "A" && character <= "Z";
}

function isLowercaseLetter(character) {
  return character >= "a" && character <= "z";
}

function splitCaseWords(value) {
  const words = [];
  let current = "";
  let previous = "";
  for (const character of value) {
    const isSeparator = WORD_SEPARATOR_CHARACTERS.includes(character);
    const isStartsNewWord =
      isUppercaseLetter(character) && isLowercaseLetter(previous);
    if (isSeparator || isStartsNewWord) {
      if (current.length > 0) {
        words.push(current);
      }
      current = isSeparator ? "" : character;
    } else {
      current += character;
    }
    previous = character;
  }
  if (current.length > 0) {
    words.push(current);
  }
  return words;
}

function capitalizeWord(word) {
  return word.length === 0
    ? word
    : word[0].toUpperCase() + word.slice(1).toLowerCase();
}

const CASE_CONVERSIONS = new Map([
  [
    "camelCase",
    (value) =>
      splitCaseWords(value)
        .map((word, position) =>
          position === 0 ? word.toLowerCase() : capitalizeWord(word)
        )
        .join(""),
  ],
  [
    "capitalize",
    (value) =>
      value.length === 0 ? value : value[0].toUpperCase() + value.slice(1),
  ],
  [
    "kebabCase",
    (value) =>
      splitCaseWords(value)
        .map((word) => word.toLowerCase())
        .join("-"),
  ],
  ["lowerCase", (value) => value.toLowerCase()],
  ["pascalCase", (value) => splitCaseWords(value).map(capitalizeWord).join("")],
  [
    "snakeCase",
    (value) =>
      splitCaseWords(value)
        .map((word) => word.toLowerCase())
        .join("_"),
  ],
  ["upperCase", (value) => value.toUpperCase()],
]);

function normalizeTransform(name, transform) {
  // Capability boundary: upstream napi types `transform?: unknown` and never
  // evaluates it ("NOT useful in JavaScript"), so the replace/separated_by
  // rejections below are a local design choice — this worker implements the
  // stable substring/convert subset and routes richer transforms to the rust
  // engine — not an upstream limitation.
  const kind = transform?.kind;
  if (kind === "replace") {
    throw requestFault(
      "unsupported_capability",
      "transform 'replace' requires the rust engine"
    );
  }
  if (kind !== "substring" && kind !== "convert") {
    throw requestFault(
      "unsupported_capability",
      `transform kind ${String(kind)} requires the rust engine`
    );
  }
  if (typeof transform.source !== "string" || transform.source.length === 0) {
    throw requestFault(
      "bad_request",
      `transform ${name} needs a metavariable source`
    );
  }
  if (kind === "convert") {
    if (
      Array.isArray(transform.separated_by) &&
      transform.separated_by.length > 0
    ) {
      throw requestFault(
        "unsupported_capability",
        "transform 'convert' with separated_by requires the rust engine"
      );
    }
    if (!CASE_CONVERSIONS.has(transform.to_case)) {
      throw requestFault(
        "unsupported_capability",
        `convert to_case ${String(transform.to_case)} requires the rust engine`
      );
    }
  }
  return {
    endChar: transform.end_char ?? null,
    kind,
    name,
    sourceName: stripMetavariableSigil(transform.source),
    startChar: transform.start_char ?? null,
    toCase: transform.to_case ?? null,
  };
}

function normalizeTransforms(transforms) {
  if (transforms === null || transforms === undefined) {
    return [];
  }
  if (typeof transforms !== "object") {
    throw requestFault("bad_request", "transforms must be an object");
  }
  return Object.entries(transforms).map(([name, transform]) =>
    normalizeTransform(name, transform)
  );
}

function buildFixPlan(fix, transformPlans) {
  if (fix === null || fix === undefined) {
    return null;
  }
  if (typeof fix === "string") {
    return { template: fix, transforms: transformPlans };
  }
  if (typeof fix !== "object") {
    throw requestFault("bad_request", "fix must be a string or an object");
  }
  // Upstream napi exposes no Fixer API; the expand_start/expand_end
  // rejections are a local design choice (rewrite expansion stays with the
  // rust engine), not an upstream limitation.
  if (fix.expand_start !== null && fix.expand_start !== undefined) {
    throw requestFault(
      "unsupported_capability",
      "fix expand_start requires the rust engine"
    );
  }
  if (fix.expand_end !== null && fix.expand_end !== undefined) {
    throw requestFault(
      "unsupported_capability",
      "fix expand_end requires the rust engine"
    );
  }
  const template = typeof fix.template === "string" ? fix.template : fix.text;
  if (typeof template !== "string") {
    throw requestFault("bad_request", "fix template must be a string");
  }
  return { template, transforms: transformPlans };
}

function normalizeSliceIndex(index, length) {
  const resolved = index < 0 ? length + index : index;
  return Math.min(Math.max(resolved, 0), length);
}

function applySubstring(value, plan) {
  const characters = [...value];
  const start = normalizeSliceIndex(plan.startChar ?? 0, characters.length);
  const end = normalizeSliceIndex(
    plan.endChar ?? characters.length,
    characters.length
  );
  return characters.slice(start, Math.max(start, end)).join("");
}

function applyTransforms(transformPlans, singleTexts) {
  for (const plan of transformPlans) {
    const sourceValue = singleTexts.get(plan.sourceName) ?? "";
    const transformed =
      plan.kind === "substring"
        ? applySubstring(sourceValue, plan)
        : CASE_CONVERSIONS.get(plan.toCase)(sourceValue);
    singleTexts.set(plan.name, transformed);
  }
}

function nodeSpan(node) {
  const range = node.range();
  return { end: range.end.index, start: range.start.index };
}

function buildMatchEnvironment(node, names, source) {
  const singleSpans = new Map();
  const multiSpans = new Map();
  for (const name of names) {
    const single = node.getMatch(name);
    if (single) {
      singleSpans.set(name, nodeSpan(single));
    } else {
      const parts = node.getMultipleMatches(name);
      if (parts.length > 0) {
        multiSpans.set(name, {
          end: nodeSpan(parts.at(-1)).end,
          start: nodeSpan(parts[0]).start,
        });
      }
    }
  }
  const singleTexts = new Map();
  for (const [name, span] of singleSpans) {
    singleTexts.set(name, source.slice(span.start, span.end));
  }
  const multiTexts = new Map();
  for (const [name, span] of multiSpans) {
    multiTexts.set(name, source.slice(span.start, span.end));
  }
  return { multiSpans, multiTexts, singleSpans, singleTexts };
}

function buildCaptures(environment, source, limits) {
  const captures = [];
  for (const [name, span] of [
    ...environment.singleSpans,
    ...environment.multiSpans,
  ]) {
    if (captures.length >= MAX_CAPTURES_PER_FINDING) {
      break;
    }
    captures.push({
      byte_end: utf8Offset(source, span.end),
      byte_start: utf8Offset(source, span.start),
      name,
      text: truncateCodePoints(
        source.slice(span.start, span.end),
        limits.max_capture_chars
      ),
    });
  }
  return captures;
}

function buildFinding(path, source, node, environment, limits) {
  const range = node.range();
  return {
    byte_end: utf8Offset(source, range.end.index),
    byte_start: utf8Offset(source, range.start.index),
    captures: buildCaptures(environment, source, limits),
    end_column: range.end.column,
    end_line: range.end.line,
    path,
    start_column: range.start.column,
    start_line: range.start.line,
    text_preview: truncateCodePoints(
      source.slice(range.start.index, range.end.index),
      limits.max_preview_chars
    ),
  };
}

function matchLongestName(template, cursor, sortedNames) {
  for (const name of sortedNames) {
    if (template.startsWith(name, cursor)) {
      return name;
    }
  }
  return null;
}

function sortedByLengthDescending(names) {
  return names.toSorted(
    (left, right) => right.length - left.length || left.localeCompare(right)
  );
}

function resolveTemplateName(template, cursor, names, isMulti) {
  if (!isMulti) {
    return matchLongestName(template, cursor, names.single);
  }
  return (
    matchLongestName(template, cursor, names.multi) ??
    matchLongestName(template, cursor, names.single)
  );
}

function renderedTextFor(name, isMulti, environment) {
  if (isMulti && environment.multiTexts.has(name)) {
    return environment.multiTexts.get(name);
  }
  return environment.singleTexts.get(name) ?? environment.multiTexts.get(name);
}

function substituteMetavariable(template, index, cursor, environment, names) {
  const isMulti = cursor - index >= MULTI_MATCH_DOLLAR_COUNT;
  const name = resolveTemplateName(template, cursor, names, isMulti);
  if (name === null) {
    return { nextIndex: cursor, rendered: template.slice(index, cursor) };
  }
  return {
    nextIndex: cursor + name.length,
    rendered: renderedTextFor(name, isMulti, environment),
  };
}

function renderFixTemplate(template, environment) {
  const names = {
    multi: sortedByLengthDescending(environment.multiTexts.keys().toArray()),
    single: sortedByLengthDescending(environment.singleTexts.keys().toArray()),
  };
  let output = "";
  let index = 0;
  while (index < template.length) {
    if (template[index] === "$") {
      let cursor = index;
      while (cursor < template.length && template[cursor] === "$") {
        cursor += 1;
      }
      const step = substituteMetavariable(
        template,
        index,
        cursor,
        environment,
        names
      );
      output += step.rendered;
      index = step.nextIndex;
    } else {
      output += template[index];
      index += 1;
    }
  }
  return output;
}

export function planEditRanges(ranges) {
  const sorted = ranges.toSorted(
    (left, right) =>
      left.byte_start - right.byte_start || right.byte_end - left.byte_end
  );
  const surviving = [];
  for (const range of sorted) {
    const container = surviving.at(-1);
    const isNested =
      container !== undefined &&
      container.byte_start <= range.byte_start &&
      range.byte_end <= container.byte_end;
    if (!isNested) {
      if (container !== undefined && range.byte_start < container.byte_end) {
        throw requestFault(
          "overlapping_edits",
          "surviving edit ranges partially overlap"
        );
      }
      surviving.push(range);
    }
  }
  return surviving;
}

function buildFileEdits(path, source, matchEntries, fixPlan) {
  const ranges = matchEntries.map((entry) => {
    const range = entry.node.range();
    return {
      byte_end: utf8Offset(source, range.end.index),
      byte_start: utf8Offset(source, range.start.index),
      entry,
    };
  });
  return planEditRanges(ranges).map((range) => {
    applyTransforms(fixPlan.transforms, range.entry.environment.singleTexts);
    return {
      byte_end: range.byte_end,
      byte_start: range.byte_start,
      inserted_text: renderFixTemplate(
        fixPlan.template,
        range.entry.environment
      ),
      path,
    };
  });
}

function foldFileResult(fileResult, names, limits, fixPlan, collector) {
  if (fileResult.error) {
    collector.errors.push(fileResult.error);
    return;
  }
  const matchEntries = [];
  for (const node of fileResult.nodes) {
    if (collector.findings.length >= limits.max_findings) {
      collector.truncated = true;
      break;
    }
    const environment = buildMatchEnvironment(node, names, fileResult.source);
    matchEntries.push({ environment, node });
    collector.findings.push(
      buildFinding(
        fileResult.path,
        fileResult.source,
        node,
        environment,
        limits
      )
    );
  }
  if (fixPlan !== null && matchEntries.length > 0) {
    collector.edits.push(
      ...buildFileEdits(
        fileResult.path,
        fileResult.source,
        matchEntries,
        fixPlan
      )
    );
  }
}

async function collectInlineFile(runtime, language, napiMatcher, file) {
  if (typeof file?.path !== "string" || typeof file?.content_b64 !== "string") {
    return {
      error: {
        message: "inline files need path and content_b64",
        path: typeof file?.path === "string" ? file.path : null,
        type: "bad_request",
      },
    };
  }
  try {
    const source = Buffer.from(file.content_b64, "base64").toString("utf-8");
    const root = await runtime.engine.parseAsync(language, source);
    return { nodes: root.root().findAll(napiMatcher), path: file.path, source };
  } catch (error) {
    return {
      error: {
        message: errorMessage(error),
        path: file.path,
        type: "parse_error",
      },
    };
  }
}

async function collectInlineFiles(runtime, language, napiMatcher, files) {
  const slots = Array.from({ length: files.length });
  const state = { cursor: 0 };
  const pump = async () => {
    if (state.cursor >= files.length) {
      return;
    }
    const slot = state.cursor;
    state.cursor += 1;
    slots[slot] = await collectInlineFile(
      runtime,
      language,
      napiMatcher,
      files[slot]
    );
    await pump();
  };
  const workerCount = Math.min(INLINE_PARSE_CONCURRENCY, files.length);
  await Promise.all(Array.from({ length: workerCount }, pump));
  return slots;
}

function resolveMirrorPaths(mirrorRoot, globPaths) {
  if (globPaths === null || globPaths === undefined) {
    return [mirrorRoot];
  }
  if (!Array.isArray(globPaths)) {
    throw requestFault("bad_request", "glob_paths must be an array or null");
  }
  return globPaths.map((globPath) => {
    if (typeof globPath !== "string" || isAbsolute(globPath)) {
      throw requestFault(
        "bad_request",
        "glob_paths entries must be relative paths"
      );
    }
    const resolved = join(mirrorRoot, globPath);
    if (relative(mirrorRoot, resolved).startsWith("..")) {
      throw requestFault(
        "bad_request",
        "glob_paths entries must stay inside mirror_root"
      );
    }
    return resolved;
  });
}

async function runFindInFiles(
  runtime,
  language,
  napiMatcher,
  paths,
  collector
) {
  const matchedFiles = [];
  const progress = { expected: null, resolveCompletion: null, seen: 0 };
  const completed = new Promise((resolve) => {
    progress.resolveCompletion = resolve;
  });
  const settleWhenDrained = () => {
    if (progress.expected !== null && progress.seen >= progress.expected) {
      progress.resolveCompletion();
    }
  };
  const onFileMatches = (failure, nodes) => {
    progress.seen += 1;
    if (failure) {
      collector.errors.push({
        message: errorMessage(failure),
        path: null,
        type: "engine_failure",
      });
    } else if (nodes.length > 0) {
      matchedFiles.push({ absolutePath: nodes[0].getRoot().filename(), nodes });
    }
    settleWhenDrained();
  };
  const expected = await runtime.engine.findInFiles(
    language,
    { matcher: napiMatcher, paths },
    onFileMatches
  );
  progress.expected = expected;
  settleWhenDrained();
  await completed;
  return matchedFiles;
}

async function collectMirrorFiles(
  runtime,
  language,
  napiMatcher,
  request,
  collector
) {
  const mirrorRoot = request.mirror_root;
  if (typeof mirrorRoot !== "string" || !isAbsolute(mirrorRoot)) {
    throw requestFault("bad_request", "mirror_root must be an absolute path");
  }
  const paths = resolveMirrorPaths(mirrorRoot, request.glob_paths);
  const matchedFiles = await runFindInFiles(
    runtime,
    language,
    napiMatcher,
    paths,
    collector
  );
  const sortedFiles = matchedFiles.toSorted((left, right) =>
    left.absolutePath.localeCompare(right.absolutePath)
  );
  return sortedFiles.map((file) => {
    try {
      return {
        nodes: file.nodes,
        path: relative(mirrorRoot, file.absolutePath),
        source: readFileSync(file.absolutePath, "utf-8"),
      };
    } catch (error) {
      return {
        error: {
          message: errorMessage(error),
          path: relative(mirrorRoot, file.absolutePath),
          type: "unreadable_file",
        },
      };
    }
  });
}

async function runStructural(runtime, request) {
  const language = resolveLanguage(runtime, request.language);
  const plan = buildMatcherPlan(request.matcher);
  validateMatcher(runtime, language, plan.napiMatcher);
  const limits = normalizeLimits(request.limits);
  const want = new Set(
    Array.isArray(request.want) && request.want.length > 0
      ? request.want
      : ["findings"]
  );
  const transformPlans = normalizeTransforms(request.transforms);
  const fixPlan = buildFixPlan(request.fix, transformPlans);
  const names = [...plan.constraintNames];
  const seen = new Set(plan.constraintNames);
  scanMetavariableNames(plan.scanText, names, seen);
  if (fixPlan !== null) {
    scanMetavariableNames(fixPlan.template, names, seen);
  }
  const collector = { edits: [], errors: [], findings: [], truncated: false };
  const inlineFiles = Array.isArray(request.files) ? request.files : [];
  const isUseMirror =
    request.mirror_root !== null && request.mirror_root !== undefined;
  const fileResults = await (isUseMirror
    ? collectMirrorFiles(
        runtime,
        language,
        plan.napiMatcher,
        request,
        collector
      )
    : collectInlineFiles(runtime, language, plan.napiMatcher, inlineFiles));
  const appliedFixPlan = want.has("edits") ? fixPlan : null;
  for (const fileResult of fileResults) {
    foldFileResult(fileResult, names, limits, appliedFixPlan, collector);
  }
  return {
    edits: collector.edits,
    engine: ENGINE_NAME,
    engine_version: runtime.engineVersion,
    errors: collector.errors,
    findings: want.has("findings") ? collector.findings : [],
    id: request.id,
    truncated: collector.truncated,
  };
}

function writeFrame(payload, onWritten) {
  let frame = Buffer.from(`${JSON.stringify(payload)}\n`, "utf-8");
  if (frame.length > MAX_FRAME_BYTES) {
    frame = Buffer.from(
      `${JSON.stringify({
        error: {
          message: "response exceeds the 8 MiB cap",
          type: "response_too_large",
        },
        id: payload.id ?? null,
      })}\n`,
      "utf-8"
    );
  }
  process.stdout.write(frame, onWritten);
}

async function handleFrame(runtime, state, frame) {
  let request;
  try {
    request = JSON.parse(frame.toString("utf-8"));
  } catch {
    writeFrame({
      error: { message: "request is not valid JSON", type: "bad_frame" },
      id: null,
    });
    return;
  }
  if (request === null || typeof request !== "object") {
    writeFrame({
      error: { message: "request must be a JSON object", type: "bad_frame" },
      id: null,
    });
    return;
  }
  const id = request.id ?? null;
  if (request.op === "ping") {
    writeFrame({
      capabilities: CAPABILITIES,
      engine: ENGINE_NAME,
      engine_version: runtime.engineVersion,
      id,
      ok: true,
    });
    return;
  }
  if (request.op === "shutdown") {
    state.isShuttingDown = true;
    writeFrame({ id, ok: true }, () => {
      process.exit(0);
    });
    return;
  }
  if (request.op === "structural") {
    try {
      writeFrame(await runStructural(runtime, request));
    } catch (error) {
      writeFrame({ error: faultPayload(error), id });
    }
    return;
  }
  writeFrame({
    error: { message: String(request.op), type: "unknown_op" },
    id,
  });
}

async function enqueueFrame(runtime, state, previousTurn, frame) {
  await previousTurn;
  await handleFrame(runtime, state, frame);
}

async function finishAfterDrain(previousTurn) {
  await previousTurn;
  process.exitCode ??= 0;
}

function rejectOversizedFrame(state) {
  writeFrame({
    error: {
      message: "request frame exceeds the 8 MiB cap",
      type: "frame_too_large",
    },
    id: null,
  });
  state.isShuttingDown = true;
  process.exitCode = PROTOCOL_ERROR_EXIT_CODE;
  process.stdin.destroy();
}

function onStdinData(runtime, state, chunk) {
  if (state.isShuttingDown) {
    return;
  }
  state.pending = Buffer.concat([state.pending, chunk]);
  if (
    state.pending.length > MAX_FRAME_BYTES &&
    !state.pending.includes(LINE_FEED_BYTE)
  ) {
    rejectOversizedFrame(state);
    return;
  }
  let newline = state.pending.indexOf(LINE_FEED_BYTE);
  while (newline !== -1 && !state.isShuttingDown) {
    const frame = state.pending.subarray(0, newline);
    state.pending = state.pending.subarray(newline + 1);
    if (frame.length > MAX_FRAME_BYTES) {
      rejectOversizedFrame(state);
      return;
    }
    state.queue = enqueueFrame(runtime, state, state.queue, frame);
    newline = state.pending.indexOf(LINE_FEED_BYTE);
  }
}

function serve(runtime) {
  const state = {
    isShuttingDown: false,
    pending: Buffer.alloc(0),
    queue: Promise.resolve(),
  };
  process.stdin.on("data", (chunk) => onStdinData(runtime, state, chunk));
  process.stdin.on("end", () => {
    state.queue = finishAfterDrain(state.queue);
  });
}

async function main() {
  try {
    const configuration = loadConfiguration(process.argv[2]);
    const runtime = await initializeRuntime(configuration);
    writeFrame({
      capabilities: CAPABILITIES,
      engine: ENGINE_NAME,
      engine_version: runtime.engineVersion,
      ready: true,
    });
    serve(runtime);
  } catch (error) {
    process.stderr.write(
      `napi worker startup failed: ${errorMessage(error)}\n`
    );
    process.exitCode = STARTUP_FAILURE_EXIT_CODE;
  }
}

// The worker may be launched through a symlinked path; compare both the raw
// and resolved forms of argv[1] against the module URL.
const isExecutedDirectly =
  typeof process.argv[1] === "string" &&
  (import.meta.url === pathToFileURL(process.argv[1]).href ||
    import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href);
if (isExecutedDirectly) {
  main();
}
