"use strict";

const crypto = require("node:crypto");
const readline = require("node:readline");
const { createRequire } = require("node:module");
const {
  dirname,
  join,
  posix: { join: posixJoin },
} = require("node:path");
const { pathToFileURL } = require("node:url");

const runtimePrefix = process.env.SOLEAUX_TYPESCRIPT_RUNTIME;

if (!runtimePrefix) {
  throw new Error("SOLEAUX_TYPESCRIPT_RUNTIME is required");
}

const PROTOCOL_VERSION = "soleaux.typescript/v1";
const MAX_TEXT = 65_536;
const MAX_PROJECT_CACHE_ENTRIES = 4;
const MAX_NATIVE_CACHE_ENTRIES = 2;
const MAX_AST_KIND_COUNTS = 256;
const runtimeRequire = createRequire(join(runtimePrefix, "package.json"));
const tsMorph = runtimeRequire("ts-morph");
const tsMorphManifest = runtimeRequire("ts-morph/package.json");
const nativeManifestPath = runtimeRequire.resolve(
  "@typescript/native/package.json"
);
const nativeRoot = dirname(nativeManifestPath);
const nativeManifest = runtimeRequire("@typescript/native/package.json");
const tsMorphProjects = new Map();
const nativeSessions = new Map();
let tsMorphEvictions = 0;
let nativeEvictions = 0;
let isShuttingDown = false;

const nativeImport = (relativePath) =>
  import(pathToFileURL(join(nativeRoot, relativePath)).href);

const sha256 = (text) =>
  crypto.createHash("sha256").update(text, "utf-8").digest("hex");

const stableDigest = (value) =>
  sha256(
    JSON.stringify(value, (_key, nested) => {
      if (nested && typeof nested === "object" && !Array.isArray(nested)) {
        return Object.fromEntries(
          Object.entries(nested).toSorted(([left], [right]) =>
            left.localeCompare(right)
          )
        );
      }
      return nested;
    })
  );

const parityDimension = (tsMorphValue, nativeValue) => {
  const tsMorphDigest = stableDigest(tsMorphValue);
  const nativeDigest = stableDigest(nativeValue);
  return {
    status: tsMorphDigest === nativeDigest ? "equal" : "different",
    ts_morph_digest: tsMorphDigest,
    native_digest: nativeDigest,
    ts_morph_count: Array.isArray(tsMorphValue) ? tsMorphValue.length : 1,
    native_count: Array.isArray(nativeValue) ? nativeValue.length : 1,
  };
};

const comparableImports = (analysis) =>
  analysis.imports
    .map((item) => [
      item.path,
      item.specifier,
      item.usage,
      item.resolved_path ?? null,
    ])
    .toSorted((left, right) =>
      JSON.stringify(left).localeCompare(JSON.stringify(right))
    );

const comparableDiagnostics = (analysis) =>
  analysis.diagnostics
    .map((item) => [
      item.path ?? null,
      item.category,
      item.code ?? null,
      item.byte_start ?? null,
      item.byte_end ?? null,
      item.message,
    ])
    .toSorted((left, right) =>
      JSON.stringify(left).localeCompare(JSON.stringify(right))
    );

const compareTypeScriptEngines = (tsMorphAnalysis, nativeAnalysis) => {
  const config = parityDimension(
    {
      config_path: tsMorphAnalysis.config_path,
      compiler_options: tsMorphAnalysis.compiler_options,
    },
    {
      config_path: nativeAnalysis.config_path,
      compiler_options: nativeAnalysis.compiler_options,
    }
  );
  const roots = parityDimension(
    tsMorphAnalysis.root_files.toSorted((left, right) =>
      left.localeCompare(right)
    ),
    nativeAnalysis.root_files.toSorted((left, right) =>
      left.localeCompare(right)
    )
  );
  const resolution = parityDimension(
    comparableImports(tsMorphAnalysis),
    comparableImports(nativeAnalysis)
  );
  const diagnostics = parityDimension(
    comparableDiagnostics(tsMorphAnalysis),
    comparableDiagnostics(nativeAnalysis)
  );
  return {
    status: [config, roots, resolution, diagnostics].every(
      (dimension) => dimension.status === "equal"
    )
      ? "equal"
      : "different",
    config,
    roots,
    resolution,
    diagnostics,
  };
};

const toAbsolute = (repositoryPath) => posixJoin("/workspace", repositoryPath);

const toRepositoryPath = (absolutePath) => {
  const normalized = absolutePath.replaceAll("\\", "/");
  return normalized.startsWith("/workspace/")
    ? normalized.slice("/workspace/".length)
    : normalized;
};

const flattenMessage = (value) => {
  if (typeof value === "string") {
    return value;
  }
  if (!value || typeof value !== "object") {
    return String(value ?? "TypeScript diagnostic");
  }
  let head = "TypeScript diagnostic";
  if (typeof value.messageText === "string") {
    head = value.messageText;
  } else if (typeof value.message === "string") {
    head = value.message;
  }
  const next = Array.isArray(value.next)
    ? value.next.map(flattenMessage).filter(Boolean)
    : [];
  return [head, ...next].join("\n");
};

const boundedText = (value) =>
  value.length <= MAX_TEXT ? value : `${value.slice(0, MAX_TEXT)}…`;

const sortedMapEntries = (entries) =>
  entries.toArray().toSorted(([left], [right]) => left.localeCompare(right));

const byteRange = (text, start, end) => ({
  byte_start: Buffer.byteLength(text.slice(0, start), "utf-8"),
  byte_end: Buffer.byteLength(text.slice(0, end), "utf-8"),
});

const locationFromNode = (node, extra = {}) => {
  const sourceFile = node.getSourceFile();
  const start = node.getStart();
  const end = node.getEnd();
  return {
    path: toRepositoryPath(sourceFile.getFilePath()),
    start,
    end,
    ...byteRange(sourceFile.getFullText(), start, end),
    kind: node.getKindName(),
    ...extra,
  };
};

const nativeLocation = (node, sourceFile, extra = {}) => {
  const start = node.getStart(sourceFile);
  const end = node.getEnd();
  return {
    path: toRepositoryPath(sourceFile.fileName),
    start,
    end,
    ...byteRange(sourceFile.text, start, end),
    kind: String(node.kind),
    ...extra,
  };
};

const requestRevision = (request) =>
  stableDigest({
    config_path: request.config_path,
    root_files: request.root_files,
    package_roots: request.package_roots,
    sources: request.sources.map((source) => [source.path, source.digest]),
  });

const projectCacheKey = (request) =>
  `${request.workspace_id}\0${request.project_id}\0${request.config_path ?? ""}`;

const identity = (
  engine,
  packageName,
  packageVersion,
  runtimeVersion,
  apiEntrypoint,
  binaryVersion
) => ({
  engine,
  package_name: packageName,
  package_version: packageVersion,
  runtime_version: runtimeVersion,
  api_entrypoint: apiEntrypoint,
  binary_version: binaryVersion,
  protocol_version: PROTOCOL_VERSION,
});

const baseCapabilities = () => ({
  ts_morph_project: true,
  ts_morph_language_service: true,
  ts_morph_definitions: true,
  ts_morph_implementations: true,
  ts_morph_references: true,
  ts_morph_types: true,
  ts_morph_signatures: true,
  ts_morph_documentation: true,
  ts_morph_calls: true,
  ts_morph_transformations: true,
  ts_morph_emit_to_memory: true,
  ts_morph_bounded_project_cache: true,
  native_async_api: true,
  native_virtual_filesystem: true,
  native_incremental_snapshots: true,
  native_program_diagnostics: true,
  native_checker_symbols: true,
  native_checker_references: true,
  native_checker_types: true,
  native_checker_signatures: true,
  native_checker_assignability: true,
  native_checker_documentation: true,
  native_calls: true,
  native_emitter: true,
  native_timing: true,
  native_ast_scanner: true,
  native_ast_visitor: true,
  native_ast_factory: true,
  native_ast_clone: true,
  native_bounded_snapshot_cache: true,
});

const sourceMap = (request) =>
  new Map(request.sources.map((source) => [source.path, source.text]));

const virtualEntries = (request) => {
  const entries = request.sources.map((source) => [
    toAbsolute(source.path),
    source.text,
  ]);
  const packageRoots = Object.entries(request.package_roots ?? {});
  for (const [packageName, packageRoot] of packageRoots) {
    const prefix = packageRoot ? `${packageRoot}/` : "";
    for (const source of request.sources) {
      if (!source.path.startsWith(prefix)) {
        continue;
      }
      const relativePath = source.path.slice(prefix.length);
      entries.push([
        posixJoin("/workspace/node_modules", packageName, relativePath),
        source.text,
      ]);
    }
  }
  return entries;
};

const createTsMorphProject = (request) => {
  const fileSystem = new tsMorph.InMemoryFileSystemHost();
  const entries = virtualEntries(request);
  for (const [filePath, text] of entries) {
    fileSystem.writeFileSync(filePath, text);
  }
  const configPath = request.config_path
    ? toAbsolute(request.config_path)
    : undefined;
  const project = new tsMorph.Project({
    fileSystem,
    ...(configPath && {
      tsConfigFilePath: configPath,
      skipAddingFilesFromTsConfig: true,
    }),
  });
  const rootFiles =
    request.root_files.length > 0
      ? request.root_files
      : request.sources
          .map((source) => source.path)
          .filter((sourcePath) =>
            [
              ".cjs",
              ".cjsx",
              ".cts",
              ".ctsx",
              ".d.ts",
              ".js",
              ".jsx",
              ".mjs",
              ".mjsx",
              ".mts",
              ".mtsx",
              ".ts",
              ".tsx",
            ].some((suffix) => sourcePath.endsWith(suffix))
          );
  for (const rootFile of rootFiles) {
    project.addSourceFileAtPathIfExists(toAbsolute(rootFile));
  }
  project.resolveSourceFileDependencies();
  return { project, rootFiles };
};

const releaseTsMorphProject = (entry) => {
  for (const sourceFile of entry.project.getSourceFiles()) {
    sourceFile.forgetDescendants();
    entry.project.removeSourceFile(sourceFile);
  }
};

const trimTsMorphProjects = () => {
  while (tsMorphProjects.size > MAX_PROJECT_CACHE_ENTRIES) {
    const oldestKey = tsMorphProjects.keys().next().value;
    const oldest = tsMorphProjects.get(oldestKey);
    tsMorphProjects.delete(oldestKey);
    if (oldest) {
      releaseTsMorphProject(oldest);
      tsMorphEvictions += 1;
    }
  }
};

const acquireTsMorphProject = (request) => {
  const revision = requestRevision(request);
  if (request.preview) {
    return {
      ...createTsMorphProject(request),
      cacheHit: false,
      disposable: true,
      revision,
    };
  }

  const key = projectCacheKey(request);
  const existing = tsMorphProjects.get(key);
  if (existing?.revision === revision) {
    tsMorphProjects.delete(key);
    tsMorphProjects.set(key, existing);
    return {
      project: existing.project,
      rootFiles: existing.rootFiles,
      cacheHit: true,
      disposable: false,
      revision,
    };
  }
  if (existing) {
    tsMorphProjects.delete(key);
    releaseTsMorphProject(existing);
  }
  const created = createTsMorphProject(request);
  tsMorphProjects.set(key, { ...created, revision });
  trimTsMorphProjects();
  return {
    ...created,
    cacheHit: false,
    disposable: false,
    revision,
  };
};

const tsMorphDiagnostic = (diagnostic) => {
  const sourceFile = diagnostic.getSourceFile();
  const start = diagnostic.getStart();
  const length = diagnostic.getLength();
  return {
    engine: "ts-morph",
    category:
      tsMorph.ts.DiagnosticCategory[diagnostic.getCategory()] ?? "unknown",
    code: diagnostic.getCode(),
    message: flattenMessage(diagnostic.getMessageText()),
    path: sourceFile ? toRepositoryPath(sourceFile.getFilePath()) : undefined,
    start,
    length,
    ...(sourceFile &&
      typeof start === "number" &&
      byteRange(
        sourceFile.getFullText(),
        start,
        start + (typeof length === "number" ? length : 0)
      )),
  };
};

const declarationNodes = (sourceFile) => [
  ...sourceFile.getFunctions(),
  ...sourceFile.getClasses(),
  ...sourceFile.getInterfaces(),
  ...sourceFile.getTypeAliases(),
  ...sourceFile.getEnums(),
  ...sourceFile.getVariableDeclarations(),
  ...sourceFile.getModules(),
];

const locationFromDocumentSpan = (span) => {
  const sourceFile = span.getSourceFile();
  const textSpan = span.getTextSpan();
  const start = textSpan.getStart();
  const end = textSpan.getEnd();
  return {
    path: toRepositoryPath(sourceFile.getFilePath()),
    start,
    end,
    ...byteRange(sourceFile.getFullText(), start, end),
    kind:
      typeof span.getKind === "function" ? String(span.getKind()) : undefined,
    name:
      typeof span.getName === "function" ? String(span.getName()) : undefined,
  };
};

const documentationForSymbol = (symbol, checker) => {
  if (!symbol) {
    return null;
  }
  try {
    const parts = symbol.compilerSymbol.getDocumentationComment(
      checker.compilerObject
    );
    const documentation = tsMorph.ts.displayPartsToString(parts);
    return documentation ? boundedText(documentation) : null;
  } catch {
    return null;
  }
};

const signatureText = (signature, declaration, checker) => {
  try {
    return boundedText(
      checker.compilerObject.signatureToString(
        signature.compilerSignature,
        declaration.compilerNode,
        tsMorph.ts.TypeFormatFlags.NoTruncation
      )
    );
  } catch {
    return null;
  }
};

const extractTsMorphCalls = (sourceFiles, checker, maxFacts, warnings) => {
  const calls = [];
  for (const sourceFile of sourceFiles) {
    for (const call of sourceFile.getDescendantsOfKind(
      tsMorph.SyntaxKind.CallExpression
    )) {
      if (calls.length >= maxFacts) {
        warnings.push(`ts-morph call limit ${maxFacts} reached`);
        return calls;
      }
      const expression = call.getExpression();
      const symbol = expression.getSymbol();
      const resolved = checker.getResolvedSignature(call);
      const start = expression.getStart();
      const end = expression.getEnd();
      const returnType = resolved?.getReturnType();
      calls.push({
        path: toRepositoryPath(sourceFile.getFilePath()),
        callee: symbol?.getName() ?? boundedText(expression.getText()),
        start,
        end,
        ...byteRange(sourceFile.getFullText(), start, end),
        signature_text: resolved
          ? signatureText(resolved, call, checker)
          : undefined,
        return_type_text: returnType
          ? boundedText(
              returnType.getText(call, tsMorph.ts.TypeFormatFlags.NoTruncation)
            )
          : undefined,
      });
    }
  }
  return calls;
};

const extractTsMorph = (request) => {
  const original = sourceMap(request);
  const started = performance.now();
  const acquired = acquireTsMorphProject(request);
  const { project, rootFiles } = acquired;
  const imports = [];
  const symbols = [];
  const warnings = [];
  const sourceFiles = project.getSourceFiles();
  const checker = project.getTypeChecker();

  try {
    for (const sourceFile of sourceFiles) {
      const repositoryPath = toRepositoryPath(sourceFile.getFilePath());
      for (const declaration of sourceFile.getImportDeclarations()) {
        const moduleSource = declaration.getModuleSpecifierSourceFile();
        imports.push({
          path: repositoryPath,
          specifier: declaration.getModuleSpecifierValue(),
          is_type_only: declaration.isTypeOnly(),
          usage: "direct_import",
          resolved_path: moduleSource
            ? toRepositoryPath(moduleSource.getFilePath())
            : null,
        });
      }
      for (const call of sourceFile.getDescendantsOfKind(
        tsMorph.SyntaxKind.CallExpression
      )) {
        const expression = call.getExpression();
        const expressionText = expression.getText();
        if (
          expressionText !== "require" &&
          expression.getKindName() !== "ImportKeyword"
        ) {
          continue;
        }
        const [argument] = call.getArguments();
        if (!argument || !tsMorph.Node.isStringLiteral(argument)) {
          continue;
        }
        imports.push({
          path: repositoryPath,
          specifier: argument.getLiteralValue(),
          is_type_only: false,
          usage: "dynamic_load",
          resolved_path: null,
        });
      }

      const exportedNames = new Set(
        sourceFile.getExportedDeclarations().keys()
      );
      for (const declaration of declarationNodes(sourceFile)) {
        if (symbols.length >= request.max_facts) {
          warnings.push(`ts-morph symbol limit ${request.max_facts} reached`);
          break;
        }
        const name =
          typeof declaration.getName === "function"
            ? declaration.getName()
            : undefined;
        if (!name) {
          continue;
        }
        const nameNode =
          typeof declaration.getNameNode === "function"
            ? declaration.getNameNode()
            : undefined;
        let typeText = null;
        try {
          typeText = boundedText(
            declaration
              .getType()
              .getText(
                declaration,
                tsMorph.ts.TypeFormatFlags.NoTruncation |
                  tsMorph.ts.TypeFormatFlags.UseAliasDefinedOutsideCurrentScope
              )
          );
        } catch {
          typeText = null;
        }
        const initializer =
          typeof declaration.getInitializer === "function"
            ? declaration.getInitializer()
            : undefined;
        const declarationSymbol =
          nameNode && typeof nameNode.getSymbol === "function"
            ? nameNode.getSymbol()
            : declaration.getSymbol?.();
        const declarations = declarationSymbol
          ? declarationSymbol
              .getDeclarations()
              .slice(0, request.max_facts)
              .map((node) => locationFromNode(node))
          : [locationFromNode(declaration)];
        let definitions = [];
        let implementations = [];
        let references = [];
        if (
          request.include_references &&
          nameNode &&
          typeof nameNode.findReferencesAsNodes === "function"
        ) {
          try {
            references = nameNode
              .findReferencesAsNodes()
              .slice(0, request.max_facts)
              .map((node) => ({
                path: toRepositoryPath(node.getSourceFile().getFilePath()),
                start: node.getStart(),
                end: node.getEnd(),
                ...byteRange(
                  node.getSourceFile().getFullText(),
                  node.getStart(),
                  node.getEnd()
                ),
              }));
          } catch (error) {
            warnings.push(
              `references unavailable for ${name}: ${error.message}`
            );
          }
          try {
            definitions =
              typeof nameNode.getDefinitions === "function"
                ? nameNode
                    .getDefinitions()
                    .slice(0, request.max_facts)
                    .map(locationFromDocumentSpan)
                : [];
            implementations =
              typeof nameNode.getImplementations === "function"
                ? nameNode
                    .getImplementations()
                    .slice(0, request.max_facts)
                    .map(locationFromDocumentSpan)
                : [];
          } catch (error) {
            warnings.push(
              `navigation unavailable for ${name}: ${error.message}`
            );
          }
        }
        const signatures = declaration
          .getType()
          .getCallSignatures()
          .slice(0, request.max_facts)
          .map((signature) => signatureText(signature, declaration, checker))
          .filter(Boolean);
        symbols.push({
          path: repositoryPath,
          name,
          kind: declaration.getKindName(),
          start: declaration.getStart(),
          end: declaration.getEnd(),
          ...byteRange(
            sourceFile.getFullText(),
            declaration.getStart(),
            declaration.getEnd()
          ),
          exported: exportedNames.has(name),
          type_text: typeText,
          value_text: initializer
            ? boundedText(initializer.getText())
            : undefined,
          documentation: documentationForSymbol(declarationSymbol, checker),
          signatures,
          declarations,
          definitions,
          implementations,
          references,
          assignable_to_self: checker.isTypeAssignableTo(
            declaration.getType(),
            declaration.getType()
          ),
        });
      }
    }

    const calls = extractTsMorphCalls(
      sourceFiles,
      checker,
      request.max_facts,
      warnings
    );
    const diagnostics = project
      .getPreEmitDiagnostics()
      .slice(0, request.max_facts)
      .map(tsMorphDiagnostic);
    let emittedFiles = [];
    if (request.include_emit) {
      const emission = project.emitToMemory();
      emittedFiles = emission.getFiles().map((file) => ({
        path: toRepositoryPath(file.filePath),
        text: file.text,
      }));
    }

    if (request.preview) {
      for (const sourceFile of sourceFiles) {
        if (request.preview.organize_imports) {
          sourceFile.organizeImports();
        }
        if (request.preview.format) {
          sourceFile.formatText();
        }
      }
      if (request.preview.rename_path) {
        const sourceFile = project.getSourceFile(
          toAbsolute(request.preview.rename_path)
        );
        const node = sourceFile?.getDescendantAtPos(
          request.preview.rename_position
        );
        if (!node || typeof node.rename !== "function") {
          throw new Error("rename target is not a renameable ts-morph node");
        }
        node.rename(request.preview.new_name);
      }
    }

    const previewedFiles = [];
    for (const sourceFile of sourceFiles) {
      const repositoryPath = toRepositoryPath(sourceFile.getFilePath());
      const before = original.get(repositoryPath);
      const after = sourceFile.getFullText();
      if (before !== undefined && before !== after) {
        previewedFiles.push({
          path: repositoryPath,
          preimage_digest: sha256(before),
          postimage_digest: sha256(after),
          text: after,
        });
      }
      sourceFile.forgetDescendants();
    }

    const compilerOptions = project.getCompilerOptions();
    const elapsedMs = performance.now() - started;
    const cache = {
      hit: acquired.cacheHit,
      disposable: acquired.disposable,
      revision: acquired.revision,
      entries: tsMorphProjects.size,
      limit: MAX_PROJECT_CACHE_ENTRIES,
      evictions: tsMorphEvictions,
      rss_bytes: process.memoryUsage().rss,
    };
    const result = {
      analysis: {
        identity: identity(
          "ts-morph",
          "ts-morph",
          tsMorphManifest.version,
          tsMorph.ts.version,
          "ts-morph.Project"
        ),
        config_path: request.config_path,
        root_files: rootFiles,
        compiler_options: compilerOptions,
        imports,
        symbols,
        calls,
        diagnostics,
        emitted_files: emittedFiles,
        previewed_files: previewedFiles,
        timing: {
          elapsed_ms: elapsedMs,
          source_files: sourceFiles.length,
        },
        cache,
        capability_evidence: {
          projects: 1,
          source_files: sourceFiles.length,
          imports: imports.length,
          symbols: symbols.length,
          calls: calls.length,
          definitions: symbols.reduce(
            (total, symbol) => total + symbol.definitions.length,
            0
          ),
          implementations: symbols.reduce(
            (total, symbol) => total + symbol.implementations.length,
            0
          ),
          references: symbols.reduce(
            (total, symbol) => total + symbol.references.length,
            0
          ),
          signatures: symbols.reduce(
            (total, symbol) => total + symbol.signatures.length,
            0
          ),
          emitted_files: emittedFiles.length,
          previewed_files: previewedFiles.length,
        },
        coverage: [
          "project",
          "config",
          "module_resolution",
          "language_service",
          "definitions",
          "implementations",
          "references",
          "types",
          "signatures",
          "documentation",
          "calls",
          "transformations",
          "emit_to_memory",
          "bounded_project_cache",
        ],
      },
      warnings,
      parity: {
        root_files: rootFiles,
        compiler_options: compilerOptions,
      },
    };
    if (acquired.disposable) {
      releaseTsMorphProject(acquired);
    }
    return result;
  } catch (error) {
    if (acquired.disposable) {
      releaseTsMorphProject(acquired);
    }
    throw error;
  }
};

const nativeDiagnosticPath = (diagnostic) => {
  if (typeof diagnostic.fileName === "string") {
    return toRepositoryPath(diagnostic.fileName);
  }
  if (typeof diagnostic.file?.fileName === "string") {
    return toRepositoryPath(diagnostic.file.fileName);
  }
  return null;
};

const nativeDiagnostic = (diagnostic, categoryName, sources) => {
  const repositoryPath = nativeDiagnosticPath(diagnostic);
  const start = typeof diagnostic.start === "number" ? diagnostic.start : null;
  const length =
    typeof diagnostic.length === "number" ? diagnostic.length : null;
  const source = repositoryPath ? sources.get(repositoryPath) : null;
  return {
    engine: "typescript-native",
    category: categoryName,
    code: typeof diagnostic.code === "number" ? diagnostic.code : null,
    message: flattenMessage(
      diagnostic.messageText ?? diagnostic.message ?? diagnostic
    ),
    path: repositoryPath,
    start,
    length,
    ...(source &&
      start !== null &&
      byteRange(source, start, start + (length ?? 0))),
  };
};

const closeNativeSession = async (session) => {
  if (session.snapshot && !session.snapshot.isDisposed()) {
    await session.snapshot.dispose();
  }
  await session.api.close();
};

const trimNativeSessions = async () => {
  while (nativeSessions.size > MAX_NATIVE_CACHE_ENTRIES) {
    const oldestKey = nativeSessions.keys().next().value;
    const oldest = nativeSessions.get(oldestKey);
    nativeSessions.delete(oldestKey);
    if (oldest) {
      await closeNativeSession(oldest);
      nativeEvictions += 1;
    }
  }
};

const updateNativeFiles = (session, nextFiles) => {
  const changed = [];
  const created = [];
  const deleted = [];
  for (const [fileName, text] of nextFiles) {
    if (!session.files.has(fileName)) {
      session.fileSystem.writeFile(fileName, text);
      created.push(fileName);
    } else if (session.files.get(fileName) !== text) {
      session.fileSystem.writeFile(fileName, text);
      changed.push(fileName);
    }
  }
  for (const fileName of session.files.keys()) {
    if (nextFiles.has(fileName)) {
      continue;
    }

    session.fileSystem.removeFile(fileName);
    deleted.push(fileName);
  }
  session.files = nextFiles;
  return { changed, created, deleted };
};

const hasNativeFileChanges = (fileChanges) =>
  "invalidateAll" in fileChanges ||
  fileChanges.changed.length > 0 ||
  fileChanges.created.length > 0 ||
  fileChanges.deleted.length > 0;

const acquireNativeSession = async (request) => {
  const native = await nativeImport("dist/api/async/api.js");
  const nativeFs = await nativeImport("dist/api/fs.js");
  const key = projectCacheKey(request);
  const revision = requestRevision(request);
  const isDisposable = Boolean(request.preview);
  let session = isDisposable ? null : (nativeSessions.get(key) ?? null);
  let fileChanges;
  if (session) {
    nativeSessions.delete(key);
    nativeSessions.set(key, session);
    fileChanges = updateNativeFiles(session, new Map(virtualEntries(request)));
  } else {
    const files = new Map(virtualEntries(request));
    const fileSystem = nativeFs.createVirtualFileSystem(
      Object.fromEntries(files)
    );
    session = {
      api: new native.API({
        cwd: "/workspace",
        fs: fileSystem,
        collectTiming: true,
      }),
      fileSystem,
      files,
      snapshot: null,
      config: null,
      revision: null,
      openFiles: new Set(),
    };
    fileChanges = { invalidateAll: true };
  }

  const cacheHit = session.revision === revision && session.snapshot;
  if (!cacheHit) {
    const configPath = request.config_path
      ? toAbsolute(request.config_path)
      : null;
    const config = configPath
      ? await session.api.parseConfigFile(configPath)
      : { fileNames: request.root_files.map(toAbsolute), options: {} };
    const desiredOpenFiles = new Set(configPath ? [] : config.fileNames);
    const openFiles = [...desiredOpenFiles].filter(
      (fileName) => !session.openFiles.has(fileName)
    );
    const closeFiles = [...session.openFiles].filter(
      (fileName) => !desiredOpenFiles.has(fileName)
    );
    const nextSnapshot = await session.api.updateSnapshot({
      ...(configPath && !session.snapshot && { openProjects: [configPath] }),
      ...(openFiles.length > 0 && { openFiles }),
      ...(closeFiles.length > 0 && { closeFiles }),
      ...(hasNativeFileChanges(fileChanges) && { fileChanges }),
    });
    const previous = session.snapshot;
    session.snapshot = nextSnapshot;
    session.config = config;
    session.revision = revision;
    session.openFiles = desiredOpenFiles;
    if (previous && !previous.isDisposed()) {
      await previous.dispose();
    }
  }

  if (!isDisposable) {
    nativeSessions.set(key, session);
    await trimNativeSessions();
  }
  return {
    session,
    cacheHit: Boolean(cacheHit),
    disposable: isDisposable,
    revision,
  };
};

const nativeTreeFacts = (sourceFile, ast, visitor, maxFacts) => {
  const identifiers = [];
  const calls = [];
  const kindCounts = new Map();
  let nodeCount = 0;
  const walk = (node) => {
    nodeCount += 1;
    const kindName = ast.SyntaxKind[node.kind] ?? String(node.kind);
    kindCounts.set(kindName, (kindCounts.get(kindName) ?? 0) + 1);
    if (ast.isIdentifier(node) && identifiers.length < maxFacts) {
      identifiers.push(node);
    }
    if (ast.isCallExpression(node) && calls.length < maxFacts) {
      calls.push(node);
    }
    return visitor.visitEachChild(node, walk);
  };
  visitor.visitNode(sourceFile, walk);
  return {
    identifiers,
    calls,
    nodeCount,
    kindCounts: Object.fromEntries(
      sortedMapEntries(kindCounts.entries()).slice(0, MAX_AST_KIND_COUNTS)
    ),
  };
};

const scanNativeSources = (request, ast) => {
  let tokenCount = 0;
  let identifierCount = 0;
  const kinds = new Map();
  for (const source of request.sources) {
    const scanner = ast.createScanner(true);
    scanner.setText(source.text);
    for (
      let token = scanner.scan();
      token !== ast.SyntaxKind.EndOfFile &&
      tokenCount < request.max_facts * request.sources.length;
      token = scanner.scan()
    ) {
      tokenCount += 1;
      if (scanner.isIdentifier()) {
        identifierCount += 1;
      }
      const kindName = ast.SyntaxKind[token] ?? String(token);
      kinds.set(kindName, (kinds.get(kindName) ?? 0) + 1);
    }
  }
  return {
    token_count: tokenCount,
    identifier_count: identifierCount,
    kind_counts: Object.fromEntries(
      sortedMapEntries(kinds.entries()).slice(0, MAX_AST_KIND_COUNTS)
    ),
  };
};

const nativeDocumentLocations = async (handles, project, maxFacts) => {
  const locations = [];
  for (const handle of handles.slice(0, maxFacts)) {
    const node = await handle.resolve(project);
    if (!node) {
      continue;
    }
    const sourceFile = node.getSourceFile();
    locations.push(
      nativeLocation(node, sourceFile, {
        kind: String(handle.kind),
      })
    );
  }
  return locations;
};

const nativeSignatureText = async (signature, project, ast) => {
  const declaration = await project.checker.signatureToSignatureDeclaration(
    signature,
    ast.SyntaxKind.CallSignature
  );
  return declaration
    ? boundedText(await project.emitter.printNode(declaration))
    : undefined;
};

const nativeStringLiteralText = (node, sourceFile) => {
  if (typeof node.text === "string") {
    return node.text;
  }
  const sourceText = node.getText(sourceFile);
  return sourceText.length >= 2 ? sourceText.slice(1, -1) : sourceText;
};

const selectNativeProject = (snapshot, configPath) => {
  const projects = snapshot.getProjects();
  if (configPath) {
    return snapshot.getProject(configPath) ?? projects[0];
  }
  return projects[0];
};

const extractNative = async (request, tsMorphSymbols) => {
  const acquired = await acquireNativeSession(request);
  const { session } = acquired;
  const ast = await nativeImport("dist/ast/index.js");
  const visitor = await nativeImport("dist/ast/visitor.js");
  const factory = await nativeImport("dist/ast/factory.generated.js");
  const clone = await nativeImport("dist/ast/clone.js");
  const sources = sourceMap(request);
  const { snapshot } = session;
  if (!snapshot) {
    throw new Error("native TypeScript snapshot is unavailable");
  }
  try {
    const configPath = request.config_path
      ? toAbsolute(request.config_path)
      : null;
    const project = selectNativeProject(snapshot, configPath);
    if (!project) {
      throw new Error("native TypeScript produced no project");
    }

    const diagnosticGroups = [
      ["syntactic", await project.program.getSyntacticDiagnostics()],
      ["bind", await project.program.getBindDiagnostics()],
      ["semantic", await project.program.getSemanticDiagnostics()],
      ["suggestion", await project.program.getSuggestionDiagnostics()],
      ["declaration", await project.program.getDeclarationDiagnostics()],
      ["program", await project.program.getProgramDiagnostics()],
      ["global", await project.program.getGlobalDiagnostics()],
      ["config", await project.program.getConfigFileParsingDiagnostics()],
    ];
    const diagnostics = diagnosticGroups
      .flatMap(([category, values]) =>
        values.map((value) => nativeDiagnostic(value, category, sources))
      )
      .slice(0, request.max_facts);

    const sourceFiles = [];
    const treeFacts = new Map();
    const imports = [];
    const sourceFileNames = await project.program.getSourceFileNames();
    for (const fileName of sourceFileNames) {
      const repositoryPath = toRepositoryPath(fileName);
      if (!sources.has(repositoryPath)) {
        continue;
      }
      const sourceFile = await project.program.getSourceFile(fileName);
      if (!sourceFile) {
        continue;
      }
      sourceFiles.push(sourceFile);
      treeFacts.set(
        repositoryPath,
        nativeTreeFacts(sourceFile, ast, visitor, request.max_facts)
      );
      for (const imported of sourceFile.imports) {
        imports.push({
          path: repositoryPath,
          specifier: nativeStringLiteralText(imported, sourceFile),
          is_type_only: Boolean(imported.parent?.importClause?.isTypeOnly),
          usage: "direct_import",
          resolved_path: null,
        });
      }
      for (const call of treeFacts.get(repositoryPath).calls) {
        const expressionText = call.expression.getText(sourceFile);
        if (
          expressionText !== "require" &&
          call.expression.kind !== ast.SyntaxKind.ImportKeyword
        ) {
          continue;
        }
        const [argument] = call.arguments;
        if (!argument || !ast.isStringLiteralLikeNode(argument)) {
          continue;
        }
        imports.push({
          path: repositoryPath,
          specifier: argument.text,
          is_type_only: false,
          usage: "dynamic_load",
          resolved_path: null,
        });
      }
    }

    const symbols = [];
    for (const candidate of tsMorphSymbols.slice(0, request.max_facts)) {
      const absolutePath = toAbsolute(candidate.path);
      const sourceFile = sourceFiles.find(
        (item) => item.fileName === absolutePath
      );
      if (!sourceFile) {
        continue;
      }
      const matchingIdentifiers = (
        treeFacts.get(candidate.path)?.identifiers ?? []
      )
        .filter(
          (identifier) =>
            identifier.getText(sourceFile) === candidate.name &&
            identifier.getStart(sourceFile) >= candidate.start &&
            identifier.getEnd() <= candidate.end
        )
        .toSorted(
          (left, right) =>
            left.getStart(sourceFile) - right.getStart(sourceFile)
        );
      const [matchingIdentifier] = matchingIdentifiers;
      if (!matchingIdentifier) {
        continue;
      }
      try {
        const symbol =
          await project.checker.getSymbolAtLocation(matchingIdentifier);
        const type =
          await project.checker.getTypeAtLocation(matchingIdentifier);
        const typeText = type
          ? boundedText(await project.checker.typeToString(type))
          : null;
        const declarations = symbol
          ? await nativeDocumentLocations(
              symbol.declarations,
              project,
              request.max_facts
            )
          : [];
        const signatures = [];
        if (type) {
          const typeSignatures = await project.checker.getSignaturesOfType(
            type,
            0
          );
          for (const signature of typeSignatures.slice(0, request.max_facts)) {
            const text = await nativeSignatureText(signature, project, ast);
            if (text) {
              signatures.push(text);
            }
          }
        }
        const references = [];
        if (request.include_references && symbol) {
          for (const referencedFile of sourceFiles) {
            const remaining = request.max_facts - references.length;
            if (remaining <= 0) {
              break;
            }
            const handles = await project.checker.getReferencesToSymbolInFile(
              referencedFile.fileName,
              symbol
            );
            const resolvedLocations = await nativeDocumentLocations(
              handles,
              project,
              remaining
            );
            references.push(...resolvedLocations);
          }
        }
        const documentation = symbol
          ? await project.checker.getDocumentationCommentOfSymbol(symbol)
          : "";
        const isAssignableToSelf = type
          ? await project.checker.isTypeAssignableTo(type, type)
          : null;
        symbols.push({
          path: candidate.path,
          name: symbol?.name ?? candidate.name,
          kind: symbol ? `SymbolFlags:${Number(symbol.flags)}` : candidate.kind,
          start: candidate.start,
          end: candidate.end,
          byte_start: candidate.byte_start,
          byte_end: candidate.byte_end,
          exported: candidate.exported,
          type_text: typeText,
          value_text: candidate.value_text,
          documentation: documentation ? boundedText(documentation) : null,
          signatures,
          declarations,
          definitions: declarations,
          implementations: [],
          references,
          assignable_to_self: isAssignableToSelf,
        });
      } catch {
        // Per-symbol coverage is counted explicitly below.
      }
    }

    const calls = [];
    for (const sourceFile of sourceFiles) {
      const repositoryPath = toRepositoryPath(sourceFile.fileName);
      for (const call of treeFacts.get(repositoryPath).calls) {
        if (calls.length >= request.max_facts) {
          break;
        }
        const { expression } = call;
        const start = expression.getStart(sourceFile);
        const end = expression.getEnd();
        const resolved = await project.checker.getResolvedSignature(call);
        const returnType = resolved
          ? await project.checker.getReturnTypeOfSignature(resolved)
          : null;
        calls.push({
          path: repositoryPath,
          callee: boundedText(expression.getText(sourceFile)),
          start,
          end,
          ...byteRange(sourceFile.text, start, end),
          signature_text: resolved
            ? await nativeSignatureText(resolved, project, ast)
            : null,
          return_type_text: returnType
            ? boundedText(await project.checker.typeToString(returnType))
            : null,
        });
      }
    }

    const emittedFiles = [];
    if (request.include_emit) {
      for (const sourceFile of sourceFiles.slice(0, request.max_facts)) {
        emittedFiles.push({
          path: `${toRepositoryPath(sourceFile.fileName)}.native.print`,
          text: await project.emitter.printNode(sourceFile),
        });
      }
    }

    const scannerEvidence = scanNativeSources(request, ast);
    const [firstSourceFile] = sourceFiles;
    const [firstStatement] = firstSourceFile?.statements ?? [];
    const factoryIdentifier = factory.createIdentifier("soleauxNativeProbe");
    const factoryClone = firstStatement
      ? factory.cloneNode(firstStatement)
      : null;
    const deepClone = firstStatement
      ? clone.getSynthesizedDeepClone(firstStatement, true)
      : null;
    const factoryText = await project.emitter.printNode(factoryIdentifier);
    const factoryCloneText = factoryClone
      ? await project.emitter.printNode(factoryClone)
      : "";
    const deepCloneText = deepClone
      ? await project.emitter.printNode(deepClone)
      : "";
    const timing = await session.api.getTimingInfo();
    let visitorNodeCount = 0;
    const visitorKindCounts = {};
    for (const item of treeFacts.values()) {
      visitorNodeCount += item.nodeCount;
      for (const [kind, count] of Object.entries(item.kindCounts)) {
        visitorKindCounts[kind] = (visitorKindCounts[kind] ?? 0) + count;
      }
    }
    return {
      analysis: {
        identity: identity(
          "typescript-native",
          "@typescript/native",
          nativeManifest.version,
          nativeManifest.version,
          "@typescript/native/unstable/async",
          nativeManifest.version
        ),
        config_path: request.config_path,
        root_files: project.rootFiles.map(toRepositoryPath),
        compiler_options: project.compilerOptions,
        imports,
        symbols,
        calls,
        diagnostics,
        emitted_files: emittedFiles,
        previewed_files: [],
        timing,
        cache: {
          hit: acquired.cacheHit,
          disposable: acquired.disposable,
          revision: acquired.revision,
          entries: nativeSessions.size,
          limit: MAX_NATIVE_CACHE_ENTRIES,
          evictions: nativeEvictions,
          snapshot_id: snapshot.id,
          rss_bytes: process.memoryUsage().rss,
        },
        capability_evidence: {
          source_files: sourceFiles.length,
          imports: imports.length,
          symbols: symbols.length,
          calls: calls.length,
          diagnostics_by_category: Object.fromEntries(
            diagnosticGroups.map(([category, values]) => [
              category,
              values.length,
            ])
          ),
          scanner: scannerEvidence,
          visitor: {
            node_count: visitorNodeCount,
            kind_counts: Object.fromEntries(
              Object.entries(visitorKindCounts)
                .toSorted(([left], [right]) => left.localeCompare(right))
                .slice(0, MAX_AST_KIND_COUNTS)
            ),
          },
          factory: {
            identifier_text: factoryText,
            clone_digest: sha256(factoryCloneText),
          },
          clone: {
            deep_clone_digest: sha256(deepCloneText),
          },
          emitted_files: emittedFiles.length,
        },
        coverage: [
          "async_api",
          "virtual_filesystem",
          "config",
          "incremental_snapshot",
          "diagnostics",
          "syntactic_diagnostics",
          "bind_diagnostics",
          "semantic_diagnostics",
          "suggestion_diagnostics",
          "declaration_diagnostics",
          "program_diagnostics",
          "global_diagnostics",
          "config_diagnostics",
          "checker_symbols",
          "checker_references",
          "checker_types",
          "checker_signatures",
          "checker_assignability",
          "checker_documentation",
          "calls",
          "scanner",
          "visitor",
          "factory",
          "clone",
          "emitter_print_node",
          "request_timing",
          "bounded_snapshot_cache",
        ],
      },
      parity: {
        root_files: project.rootFiles.map(toRepositoryPath),
        compiler_options: project.compilerOptions,
      },
    };
  } finally {
    if (acquired.disposable) {
      await closeNativeSession(session);
    }
  }
};

const capabilities = async () => {
  await Promise.all([
    nativeImport("dist/api/async/api.js"),
    nativeImport("dist/api/fs.js"),
  ]);
  const [ast, visitor, factory, clone] = await Promise.all([
    nativeImport("dist/ast/index.js"),
    nativeImport("dist/ast/visitor.js"),
    nativeImport("dist/ast/factory.generated.js"),
    nativeImport("dist/ast/clone.js"),
  ]);
  const scanner = ast.createScanner(true, undefined, "const probe = 1;");
  let scannedTokens = 0;
  for (
    let token = scanner.scan();
    token !== ast.SyntaxKind.EndOfFile;
    token = scanner.scan()
  ) {
    scannedTokens += 1;
  }
  const identifier = factory.createIdentifier("soleauxCapabilityProbe");
  let visitedNodes = 0;
  visitor.visitNode(identifier, (node) => {
    visitedNodes += 1;
    return visitor.visitEachChild(node, (child) => child);
  });
  const shallowClone = factory.cloneNode(identifier);
  const deepClone = clone.getSynthesizedDeepClone(identifier, true);
  return {
    protocol_version: PROTOCOL_VERSION,
    identities: {
      ts_morph: identity(
        "ts-morph",
        "ts-morph",
        tsMorphManifest.version,
        tsMorph.ts.version,
        "ts-morph.Project"
      ),
      native: identity(
        "typescript-native",
        "@typescript/native",
        nativeManifest.version,
        nativeManifest.version,
        "@typescript/native/unstable/async",
        nativeManifest.version
      ),
    },
    capabilities: baseCapabilities(),
    capability_evidence: {
      native_scanner_tokens: scannedTokens,
      native_visitor_nodes: visitedNodes,
      native_factory_identifier: identifier.text,
      native_factory_clone_kind: Number(shallowClone.kind),
      native_deep_clone_kind: Number(deepClone.kind),
      ts_morph_project_constructor: typeof tsMorph.Project === "function",
      ts_morph_compiler_version: tsMorph.ts.version,
    },
    cache_limits: {
      ts_morph_projects: MAX_PROJECT_CACHE_ENTRIES,
      native_snapshots: MAX_NATIVE_CACHE_ENTRIES,
    },
  };
};

const analyze = async (request) => {
  if (request.protocol_version !== PROTOCOL_VERSION) {
    throw new Error(`unsupported protocol ${request.protocol_version}`);
  }
  const tsMorphResult = await extractTsMorph(request);
  const nativeResult = await extractNative(
    request,
    tsMorphResult.analysis.symbols
  );
  const parity = compareTypeScriptEngines(
    tsMorphResult.analysis,
    nativeResult.analysis
  );
  const warnings = [...tsMorphResult.warnings];
  const tsRoots = new Set(tsMorphResult.parity.root_files);
  const nativeRoots = new Set(nativeResult.parity.root_files);
  if (
    tsRoots.size !== nativeRoots.size ||
    [...tsRoots].some((rootFile) => !nativeRoots.has(rootFile))
  ) {
    warnings.push("TS6 and native TypeScript root-file sets differ");
  }
  return {
    protocol_version: PROTOCOL_VERSION,
    workspace_id: request.workspace_id,
    project_id: request.project_id,
    ts_morph: tsMorphResult.analysis,
    native: nativeResult.analysis,
    capabilities: baseCapabilities(),
    parity,
    warnings,
  };
};

const respond = (payload) => {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
};

const executeFrame = (frame) => {
  if (frame.operation === "capabilities") {
    return capabilities();
  }
  if (frame.operation === "analyze") {
    return analyze(frame.request);
  }
  throw new Error(`unknown operation ${frame.operation}`);
};

const input = readline.createInterface({
  input: process.stdin,
  crlfDelay: Infinity,
});

input.on("line", async (line) => {
  let frame;
  try {
    frame = JSON.parse(line);
    const result = await executeFrame(frame);
    respond({ id: frame.id, status: "ok", result });
  } catch (error) {
    respond({
      id: frame?.id ?? null,
      status: "error",
      error: {
        type: error?.name ?? "Error",
        message: error?.message ?? String(error),
      },
    });
  }
});

const shutdown = async () => {
  if (isShuttingDown) {
    return;
  }
  isShuttingDown = true;
  input.close();
  for (const entry of tsMorphProjects.values()) {
    releaseTsMorphProject(entry);
  }
  tsMorphProjects.clear();
  const sessions = nativeSessions.values().toArray();
  nativeSessions.clear();
  await Promise.allSettled(sessions.map(closeNativeSession));
};

process.once("SIGTERM", () => {
  void shutdown().finally(() => {
    process.exit(0);
  });
});
process.once("SIGINT", () => {
  void shutdown().finally(() => {
    process.exit(0);
  });
});
