// Upstream claim registry hygiene for the soleaux telemetry surface.
//
// This script does NOT verify claims against upstream sources. It checks that
// the claim registry is well-formed, that every canonical URL cited in
// telemetry files is registered, that claim annotations reference known ids,
// and that files carrying upstream-dependent language are covered by some
// claim or product policy. Content verification against upstream is a human
// attestation recorded in lastVerifiedAt; stale attestations are reported as
// warnings so an untouched repository never goes red on a calendar schedule.

import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { extname, join } from "node:path";

const MS_PER_DAY = 86_400_000;
const FIRST_CAPTURE_GROUP = 1;
const SINGLE_CHARACTER = 1;
const STRING_START = 0;
const EMPTY_LENGTH = 0;
const EXIT_FAILURE = 1;
const PATH_RESOLVER = "/usr/bin/env";

const TELEMETRY_ROOT = join(import.meta.dirname, "..");

function git(arguments_, cwd = TELEMETRY_ROOT) {
  return execFileSync(PATH_RESOLVER, ["git", ...arguments_], {
    cwd,
  }).toString("utf-8");
}

const REPO_ROOT = git(["rev-parse", "--show-toplevel"]).trim();
const TELEMETRY_PREFIX = git(["rev-parse", "--show-prefix"]).trim();
const REGISTRY_PATH = join(TELEMETRY_ROOT, "config", "upstream-claims.json");
const SOURCES_PATH = join(TELEMETRY_ROOT, "config", "canonical-sources.json");
const registry = JSON.parse(readFileSync(REGISTRY_PATH, "utf-8"));
const sourceRegistry = existsSync(SOURCES_PATH)
  ? JSON.parse(readFileSync(SOURCES_PATH, "utf-8"))
  : { sources: [] };

const errors = [];
const warnings = [];
const today = new Date();

const volatilityDays = { high: 30, medium: 90, low: 365 };
const evidenceClasses = new Set([
  "canonical-fact",
  "measured",
  "derived",
  "product-policy",
  "estimate",
  "unknown",
]);
const exemptPrefixes = [
  ".git/",
  ".next/",
  "node_modules/",
  "target/",
  "config/",
];
const auditableExtensions = new Set([
  ".md",
  ".mdx",
  ".ts",
  ".tsx",
  ".js",
  ".jsx",
  ".mjs",
  ".cjs",
  ".py",
  ".rs",
  ".toml",
  ".yaml",
  ".yml",
  ".json",
  ".sh",
]);

function trackedFiles() {
  return git(["ls-files", "-z", "--", TELEMETRY_PREFIX], REPO_ROOT)
    .split("\0")
    .filter(Boolean);
}

function canonicalHost(url) {
  try {
    const parsed = new URL(url);
    return parsed.hostname.toLowerCase();
  } catch {
    return null;
  }
}

function isOfficialHost(host) {
  const domains = registry.officialDomains ?? [];
  return domains.some(
    (domain) => host === domain || host.endsWith(`.${domain}`)
  );
}

function daysSince(dateValue) {
  const value = new Date(`${dateValue}T00:00:00Z`);
  return Math.floor((today.valueOf() - value.valueOf()) / MS_PER_DAY);
}

const claimIds = new Set();
const registeredUrls = new Set();
const registeredSources = sourceRegistry.sources ?? [];
for (const source of registeredSources) {
  registeredUrls.add(String(source.url).replace(/\/$/u, ""));
}
const registeredEndpoints = registry.endpointUrls ?? [];
for (const endpoint of registeredEndpoints) {
  registeredUrls.add(String(endpoint).replace(/\/$/u, ""));
}
const coveredPaths = new Set();

const claims = registry.claims ?? [];
for (const claim of claims) {
  if (!claim.id || claimIds.has(claim.id)) {
    errors.push(`Duplicate or missing claim id: ${claim.id ?? "<missing>"}`);
  }
  claimIds.add(claim.id);
  if (!evidenceClasses.has(claim.evidenceClass)) {
    errors.push(`${claim.id}: invalid evidenceClass ${claim.evidenceClass}`);
  }
  if (claim.evidenceClass !== "canonical-fact") {
    errors.push(`${claim.id}: registry claims must use canonical-fact`);
  }
  const host = canonicalHost(claim.url);
  if (!host) {
    errors.push(`${claim.id}: invalid URL ${claim.url}`);
  } else if (!isOfficialHost(host)) {
    errors.push(`${claim.id}: URL host ${host} is not in officialDomains`);
  }
  registeredUrls.add(String(claim.url).replace(/\/$/u, ""));
  if (!claim.statement) {
    errors.push(`${claim.id}: statement is required`);
  }
  if (!claim.lastVerifiedAt) {
    errors.push(`${claim.id}: lastVerifiedAt is required`);
  }
  if (!claim.verificationMethod) {
    errors.push(`${claim.id}: verificationMethod is required`);
  }
  const maxAge = claim.maxAgeDays ?? volatilityDays[claim.volatility];
  if (!maxAge) {
    errors.push(
      `${claim.id}: volatility must be high, medium, low, or maxAgeDays must be set`
    );
  } else if (claim.lastVerifiedAt && daysSince(claim.lastVerifiedAt) > maxAge) {
    warnings.push(
      `${claim.id}: attestation is ${daysSince(claim.lastVerifiedAt)} days old (review every ${maxAge}); re-verify against the canonical source`
    );
  }
  if (!Array.isArray(claim.affectedPaths) || !claim.affectedPaths.length) {
    errors.push(`${claim.id}: affectedPaths must not be empty`);
  }
  const claimAffectedPaths = claim.affectedPaths ?? [];
  for (const path of claimAffectedPaths) {
    coveredPaths.add(path);
    if (!existsSync(join(REPO_ROOT, path))) {
      errors.push(`${claim.id}: affected path does not exist: ${path}`);
    }
  }
}

const productPolicies = registry.productPolicies ?? [];
for (const policy of productPolicies) {
  if (!policy.id || claimIds.has(policy.id)) {
    errors.push(`Duplicate or missing policy id: ${policy.id ?? "<missing>"}`);
  }
  claimIds.add(policy.id);
  if (policy.evidenceClass !== "product-policy") {
    errors.push(
      `${policy.id}: product policy must use evidenceClass product-policy`
    );
  }
  if (!policy.methodologyVersion) {
    errors.push(`${policy.id}: methodologyVersion is required`);
  }
  if (!Array.isArray(policy.parameters) || !policy.parameters.length) {
    errors.push(`${policy.id}: parameters must not be empty`);
  }
  const policyAffectedPaths = policy.affectedPaths ?? [];
  for (const path of policyAffectedPaths) {
    coveredPaths.add(path);
    if (!existsSync(join(REPO_ROOT, path))) {
      errors.push(`${policy.id}: affected path does not exist: ${path}`);
    }
  }
}

const files = trackedFiles().filter(
  (file) =>
    auditableExtensions.has(extname(file).toLowerCase()) &&
    exemptPrefixes.every(
      (prefix) => !(file === prefix || file.startsWith(prefix))
    )
);
const upstreamUrlPattern = /https?:\/\/[^\s<>"'`)\]]+/gu;
const claimAnnotationPattern =
  /(?:upstream-claim|claim-id|source-id)\s*[:=]\s*["'`]?([a-z0-9][a-z0-9._-]+)/giu;
const externalClaimKeywordPatterns = [
  /\b(API|SDK|provider|model|context window|token|pricing|price|quota|limit|reset)\b/iu,
  /\b(subscription|protocol|standard|requires?|supports?|deprecated|security|TLS|OAuth|OIDC)\b/iu,
  /\b(SAML|SCIM|CPU|memory|operating system)\b/iu,
];
const trailingPunctuationPattern = /[.,;:]$/u;

function stripTrailingPunctuation(value) {
  let result = value;
  while (trailingPunctuationPattern.test(result)) {
    result = result.slice(STRING_START, result.length - SINGLE_CHARACTER);
  }
  if (result.endsWith("/")) {
    result = result.slice(STRING_START, result.length - SINGLE_CHARACTER);
  }
  return result;
}

function reportUnregisteredUrls(file, content) {
  const matches = content.match(upstreamUrlPattern) ?? [];
  for (const raw of matches) {
    const url = stripTrailingPunctuation(raw);
    const host = canonicalHost(url);
    if (host && isOfficialHost(host) && !registeredUrls.has(url)) {
      errors.push(`${file}: unregistered canonical URL ${url}`);
    }
  }
}

for (const file of files) {
  const content = readFileSync(join(REPO_ROOT, file), "utf-8");
  const annotations = content
    .matchAll(claimAnnotationPattern)
    .map((match) => match[FIRST_CAPTURE_GROUP])
    .toArray();
  for (const id of annotations) {
    if (!claimIds.has(id)) {
      errors.push(`${file}: references unknown claim or policy id ${id}`);
    }
  }

  reportUnregisteredUrls(file, content);

  if (
    externalClaimKeywordPatterns.some((pattern) => pattern.test(content)) &&
    !coveredPaths.has(file) &&
    annotations.length === EMPTY_LENGTH
  ) {
    warnings.push(
      `${file}: contains upstream-dependent language but is not listed in any claim or product-policy affectedPaths`
    );
  }
}

if (warnings.length) {
  console.warn(`\nRegistry hygiene warnings (${warnings.length}):`);
  for (const warning of warnings) {
    console.warn(`  - ${warning}`);
  }
}

if (errors.length) {
  console.error(`\nRegistry hygiene failed (${errors.length}):`);
  for (const error of errors) {
    console.error(`  - ${error}`);
  }
  process.exit(EXIT_FAILURE);
}

console.log(
  `Registry hygiene passed: ${claims.length} canonical claims, ${productPolicies.length} product policies, ${files.length} tracked telemetry files.`
);
