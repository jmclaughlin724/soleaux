#!/usr/bin/env node

import { readFile, readdir, stat, writeFile, mkdir } from "node:fs/promises";
import { resolve, extname, dirname } from "node:path";
import { pathToFileURL } from "node:url";
import { analyzeScan, normalizeEvent, renderMarkdown } from "./analyzer.mjs";
import {
  detectProvider,
  parseClaudeTranscript,
  parseCodexTranscript,
} from "./providers.mjs";

const summaryNumberFormat = new Intl.NumberFormat("en-US");

const EXIT_FAILURE = 1;
const JSON_INDENT = 2;
const CLI_ARGUMENT_OFFSET = 2;
const ENTRYPOINT_ARGUMENT_INDEX = 1;
const LINE_NUMBER_OFFSET = 1;
const VALUE_ARGUMENT_OFFSET = 1;
const ARGUMENTS_PER_OPTION = 2;
const IGNORED_DIRECTORIES = new Set([
  "node_modules",
  ".git",
  ".next",
  "target",
]);
const SCAN_INPUT_EXTENSIONS = new Set([".json", ".jsonl", ".ndjson"]);
const PROVIDER_OPTIONS = new Set(["auto", "claude", "codex", "generic"]);

function usage() {
  console.log(`Soleaux Evidence Scan

Usage:
  soleaux-scan --input <file-or-directory> [--input <path> ...]
               [--provider auto|claude|codex|generic]
               [--daemon http://127.0.0.1:43120]
               [--quota <quota.json>]
               [--output .soleaux/reports/scan]

Accepted input:
  JSON, JSONL and NDJSON provider events, documented API usage objects,
  provider-reported quota snapshots, process samples, and daemon endpoints.

Outputs:
  <output>.json contains measured and deterministic derived fields.
  <output>.md contains the evidence report.

The scanner does not estimate wasted tokens, subscription time, or projected
savings without controlled before/after validation data.
`);
}

function applyValueOption(options, argument, value) {
  switch (argument) {
    case "--input":
    case "-i": {
      options.inputs.push(value);
      return;
    }
    case "--quota": {
      options.quotaFiles.push(value);
      return;
    }
    case "--provider": {
      options.provider = value;
      return;
    }
    case "--daemon": {
      options.daemon = value;
      return;
    }
    case "--output":
    case "-o": {
      options.output = value;
      return;
    }
    default: {
      throw new Error(`Unknown argument: ${argument}`);
    }
  }
}

function parseArguments(argv) {
  const options = {
    inputs: [],
    quotaFiles: [],
    provider: "auto",
    output: ".soleaux/reports/scan",
    daemon: null,
  };
  let index = 0;
  while (index < argv.length) {
    const argument = argv[index];
    if (argument === "--help" || argument === "-h") {
      return { help: true };
    }
    applyValueOption(options, argument, argv[index + VALUE_ARGUMENT_OFFSET]);
    index += ARGUMENTS_PER_OPTION;
  }
  return options;
}

async function walk(path) {
  const metadata = await stat(path);
  if (metadata.isFile()) {
    return [path];
  }
  if (!metadata.isDirectory()) {
    return [];
  }
  const entries = await readdir(path, { withFileTypes: true });
  const children = entries
    .filter((entry) => !IGNORED_DIRECTORIES.has(entry.name))
    .map((entry) => resolve(path, entry.name));
  const nested = await Promise.all(children.map((child) => walk(child)));
  return nested.flat();
}

function decode(content, file) {
  const extension = extname(file).toLowerCase();
  if (extension === ".jsonl" || extension === ".ndjson") {
    return content
      .split(/\r?\n/u)
      .filter(Boolean)
      .map((line, index) => {
        try {
          return JSON.parse(line);
        } catch (error) {
          throw new Error(
            `${file}:${index + LINE_NUMBER_OFFSET}: ${error.message}`,
            { cause: error }
          );
        }
      });
  }
  return JSON.parse(content);
}

function absorbEventArrays(value, bucket, source) {
  if (Array.isArray(value.events)) {
    for (const event of value.events) {
      bucket.events.push(normalizeEvent(event, source));
    }
  }
  if (Array.isArray(value.usageEvents)) {
    for (const event of value.usageEvents) {
      bucket.events.push(normalizeEvent(event, source));
    }
  }
}

function absorbSampleArrays(value, bucket) {
  if (Array.isArray(value.processSamples)) {
    bucket.processSamples.push(...value.processSamples);
  }
  if (Array.isArray(value.processes)) {
    bucket.processSamples.push(...value.processes);
  }
  if (Array.isArray(value.quotas)) {
    bucket.quotas.push(...value.quotas);
  }
}

function isUsageEvent(value) {
  const hasTokenFields =
    value.usage ||
    value.totalTokens !== undefined ||
    value.inputTokens !== undefined;
  return Boolean(hasTokenFields || value.toolName || value.tool_name);
}

function isProcessSample(value) {
  return (
    value.cpuPercent !== undefined || value.residentMemoryBytes !== undefined
  );
}

function isQuotaSnapshot(value) {
  const hasWindowFields =
    value.resetsAt !== undefined || value.durationSeconds !== undefined;
  return hasWindowFields || value.utilizationPercent !== undefined;
}

function absorbSingleton(value, bucket, source) {
  if (isUsageEvent(value)) {
    bucket.events.push(normalizeEvent(value, source));
  } else if (isProcessSample(value)) {
    bucket.processSamples.push(value);
  } else if (isQuotaSnapshot(value)) {
    bucket.quotas.push(value);
  }
}

function absorbNormalized(value, bucket, source) {
  if (Array.isArray(value)) {
    for (const item of value) {
      absorbNormalized(item, bucket, source);
    }
    return;
  }
  if (!value || typeof value !== "object") {
    return;
  }
  absorbEventArrays(value, bucket, source);
  absorbSampleArrays(value, bucket);
  absorbSingleton(value, bucket, source);
}

function absorbProvider(value, bucket, source, providerOption) {
  const provider =
    providerOption === "auto" ? detectProvider(value) : providerOption;
  if (provider === "claude") {
    bucket.events.push(...parseClaudeTranscript(value, source));
  } else if (provider === "codex") {
    bucket.events.push(...parseCodexTranscript(value, source));
  } else {
    absorbNormalized(value, bucket, source);
  }
}

async function fetchJson(url) {
  const response = await fetch(url, {
    headers: { accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`${url} returned ${response.status}`);
  }
  return response.json();
}

async function fetchJsonOrEmpty(url) {
  try {
    return await fetchJson(url);
  } catch {
    // Each daemon endpoint is optional; an unavailable section contributes
    // no records, matching the previous per-endpoint catch behavior.
    return [];
  }
}

async function loadDaemon(base, bucket) {
  // --daemon takes the bare origin; the API prefix is appended here.
  const root = `${base.replace(/\/$/u, "")}/api/v1`;
  const [events, quotas, processes] = await Promise.all([
    fetchJsonOrEmpty(`${root}/usage/events`),
    fetchJsonOrEmpty(`${root}/quotas`),
    fetchJsonOrEmpty(`${root}/processes`),
  ]);
  absorbNormalized(
    { events, quotas, processSamples: processes },
    bucket,
    "soleaux-daemon"
  );
}

async function readInputFiles(input) {
  const walked = await walk(resolve(input));
  const files = walked.filter((file) =>
    SCAN_INPUT_EXTENSIONS.has(extname(file).toLowerCase())
  );
  const contents = await Promise.all(
    files.map((file) => readFile(file, "utf-8"))
  );
  return files.map((file, index) => ({ file, content: contents[index] }));
}

async function main() {
  const options = parseArguments(process.argv.slice(CLI_ARGUMENT_OFFSET));
  if (options.help) {
    usage();
    return;
  }
  if (!options.inputs.length && !options.daemon) {
    usage();
    throw new Error("Provide at least one --input or --daemon source");
  }
  if (!PROVIDER_OPTIONS.has(options.provider)) {
    throw new Error("--provider must be auto, claude, codex, or generic");
  }

  const bucket = { events: [], quotas: [], processSamples: [] };
  const inputFileGroups = await Promise.all(
    options.inputs.map((input) => readInputFiles(input))
  );
  for (const documents of inputFileGroups) {
    for (const { file, content } of documents) {
      absorbProvider(decode(content, file), bucket, file, options.provider);
    }
  }
  const quotaDocuments = await Promise.all(
    options.quotaFiles.map(async (quotaFile) => ({
      file: quotaFile,
      content: await readFile(resolve(quotaFile), "utf-8"),
    }))
  );
  for (const { file, content } of quotaDocuments) {
    absorbNormalized(decode(content, file), bucket, file);
  }
  if (options.daemon) {
    await loadDaemon(options.daemon, bucket);
  }

  const eventsById = new Map(bucket.events.map((event) => [event.id, event]));
  const report = analyzeScan({
    events: eventsById.values().toArray(),
    quotas: bucket.quotas,
    processSamples: bucket.processSamples,
  });

  const output = resolve(options.output);
  await mkdir(dirname(output), { recursive: true });
  await writeFile(
    `${output}.json`,
    `${JSON.stringify(report, null, JSON_INDENT)}\n`
  );
  await writeFile(`${output}.md`, `${renderMarkdown(report)}\n`);

  console.log(
    `Soleaux scan complete\nEvents: ${report.scan.eventCount}\nObservations: ${report.observations.length}\nTotal tokens: ${summaryNumberFormat.format(report.totals.totalTokens)}\nReport: ${output}.md`
  );
}

const runCli = async () => {
  try {
    await main();
  } catch (error) {
    const message = Error.isError(error)
      ? error.message
      : "unknown scan failure";
    process.stderr.write(`soleaux-scan: ${message}\n`);
    process.exitCode = EXIT_FAILURE;
  }
};

if (
  process.argv[ENTRYPOINT_ARGUMENT_INDEX] !== undefined &&
  import.meta.url ===
    pathToFileURL(process.argv[ENTRYPOINT_ARGUMENT_INDEX]).href
) {
  void runCli();
}
