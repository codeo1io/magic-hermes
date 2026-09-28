import { readdirSync } from "node:fs";

const EXPOSED = [
  "openDatabase",
  "closeQuietly",
  "loadPiConfig",
  "ensureProjectRegisteredFromPiDirectory",
  "resolveProjectIdentityForSession",
  "recordSessionProjectIdentity",
  "recordHistorianRun",
  "registerMagicContextTools",
  "convertEntriesToRawMessages",
  "indexMessagesAfterOrdinal",
  "getLastIndexedOrdinal",
  "withRawMessageProvider",
  "readSessionChunk",
  "buildCanonicalChunkTextFromFts",
  "getCompartments",
  "getLastCompartmentEndMessage",
  "getMemoriesByProject",
  "trimMemoriesToBudgetV2",
  "renderMemoryBlockV2",
  "appendCompartments",
  "insertCompartmentEvents",
  "insertUserMemoryCandidates",
  "insertPrimerCandidates",
  "promoteSessionFactsDurable",
  "queueDropsForCompartmentalizedMessages",
  "recordProtectedTailPublicationFloor",
  "onNoteTrigger",
  "embedPromotedFacts",
  "embedAndStoreCompartmentChunks",
  "getProjectEmbeddingSnapshot",
  "parseSourceMessageTime",
  "isDreamerRunnable",
  "userMemoryCollectionEnabled",
  "deriveHistorianChunkTokens",
  "resolveHistorianContextLimit",
  "withContentLanguageDirective",
  "createPromptSurfaceRuntime",
  "buildMagicContextBlock",
  "composeMagicContextSystemPrompt",
  "processSystemPromptForCache",
  "buildReferenceBlocks",
  "buildCompartmentAgentPrompt",
  "validateHistorianOutput",
  "buildHistorianRepairPrompt",
  "buildHistorianEditorPrompt",
  "COMPARTMENT_AGENT_SYSTEM_PROMPT",
  "HISTORIAN_EDITOR_SYSTEM_PROMPT",
  "resolveHistorianFromConfig",
  "createScheduler",
  "resolvePiHistorianTriggerInputs",
  "checkCompartmentTrigger",
  "getOrCreateSessionMeta",
  "updateSessionMeta",
  "resolveCacheTtl",
  "escalationBands",
  "createTagger",
  "createPiTranscript",
  "tagTranscript",
  "applyPendingOperations",
  "applyFlushedStatuses",
  "getProtectionWindowForSession",
  "getActiveTagsBySession",
  "getPendingOps",
  "acquireCompartmentLease",
  "renewCompartmentLease",
  "releaseCompartmentLease",
  "resolveAutoSearchFromConfig",
  "runAutoSearchHintForPi",
  "injectPiTemporalMarkers",
  "injectM0M1Pi",
  "parseCacheTtl",
  "isPiHardCacheExpired",
  "applyNoteNudges",
  "sweepGitCommits",
  "embedUnembeddedMemoriesForProject",
  "sweepStaleEmbeddingIdentitiesForProject",
  "runDueCompiledSmartNoteChecks",
  "acquireLease",
  "getEmbeddingCoverageStatus",
  "runEmbedDrain",
  "buildDreamTaskRuntimeConfigs",
  "createDreamTaskExecutor",
  "runDueTasksForProject",
  "runManualDream",
  "leaseKeyFor",
  "releaseLease",
];

const FALLBACK_EXPORTS = [
  "export {",
  "  openDatabase as __mh_openDatabase,",
  "  closeQuietly as __mh_closeQuietly,",
  "  loadPiConfig as __mh_loadPiConfig,",
  "  resolveHistorianFromConfig as __mh_resolveHistorianFromConfig",
  "};"
].join("\n");

// Modules of the adapter bundle a name can be re-exported from when it is
// not a top-level binding of index.js. Upstream 0.43.x code-splits the
// session-chunk family into its own chunk (read-session-chunk-*.js) while
// earlier series exported everything from index.js; keep both shapes
// working. Chunk file names are content-hashed and drift per release, so
// match by stable prefix rather than exact name.
const SPLIT_MODULE_PREFIXES = ["read-session-chunk-"];

// Stable specifiers for the split modules. The adapter URL is set by the
// runtime before this loader loads, so the directory is derivable; chunk
// names are resolved lazily in load() because content hashes drift per
// upstream release.
function splitModuleUrls(adapterUrl) {
  const baseUrl = new URL(".", adapterUrl);
  const names = readdirSync(baseUrl).filter((name) =>
    SPLIT_MODULE_PREFIXES.some((prefix) => name.startsWith(prefix) && name.endsWith(".js"))
  );
  return names.map((name) => new URL(name, baseUrl).href);
}

const TUI_SOURCE = [
  "export class Box {}",
  "export class Text {}",
  "export const matchesKey = () => false;",
  "export const truncateToWidth = (value) => String(value ?? '');",
  "export const visibleWidth = (value) => String(value ?? '').length;"
].join("\n");
const TUI_URL = "data:text/javascript," + encodeURIComponent(TUI_SOURCE);

export async function resolve(specifier, context, nextResolve) {
  if (specifier === "@earendil-works/pi-tui") {
    return { url: TUI_URL, shortCircuit: true };
  }
  return nextResolve(specifier, context);
}

export async function load(url, context, nextLoad) {
  const result = await nextLoad(url, context);
  if (url.split("?")[0] !== process.env.MAGIC_HERMES_ADAPTER_URL) {
    return result;
  }

  let source = String(result.source);
  const piHarnessForms = [
    'setHarness("pi");',
    "setHarness('pi');",
    "setHarness(PI_HARNESS_KIND);",
  ];
  const matchedForm = piHarnessForms.find((form) => source.includes(form));
  if (matchedForm === undefined) {
    throw new Error("Unsupported Magic Context adapter: Pi harness initializer not found");
  }
  source = source.replace(matchedForm, 'setHarness("hermes");');

  // Names that are top-level bindings of index.js can be re-exported
  // directly. Upstream 0.43.x code-splits some exports into sibling chunks;
  // those must be re-exported FROM the chunk module (`export { x } from
  // "./chunk.js"`) because they are not in scope in index.js. Detect scope
  // by parsing the module record's import bindings and declarations —
  // cheap and sufficient: a name we append to `export { ... }` must appear
  // as a binding, not merely as a word inside a function body (e.g. the
  // esbuild alias readSessionChunk2).
  const topLevel = new Set();
  for (const match of source.matchAll(/import\s*\{([^}]*)\}\s*from/g)) {
    for (const part of match[1].split(",")) {
      const seg = part.split(/\s+as\s+/);
      const name = (seg.length > 1 ? seg[1] : seg[0]).trim();
      if (name) topLevel.add(name);
    }
  }
  for (const match of source.matchAll(/^(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)/gm)) {
    topLevel.add(match[1]);
  }
  for (const match of source.matchAll(/^(?:export\s+)?(?:const|let|var|class)\s+([A-Za-z_$][\w$]*)/gm)) {
    topLevel.add(match[1]);
  }

  const direct = [];
  const deferred = [];
  for (const name of EXPOSED) {
    if (topLevel.has(name)) direct.push(name);
    else deferred.push(name);
  }

  let suffix = "";
  if (direct.length > 0) {
    suffix += "\nexport { " + direct.map((name) => name + " as __mh_" + name).join(", ") + " };\n";
  }
  if (deferred.length > 0) {
    // Resolve deferred names from the split modules. Build one
    // `export { ... } from "<chunk>"` clause per chunk that provides at
    // least one deferred name; a name found nowhere stays undefined at
    // call time — the runtime's mc() throws a precise error naming it,
    // matching the pre-existing fail-closed contract.
    const remaining = new Set(deferred);
    for (const chunkUrl of splitModuleUrls(url)) {
      if (remaining.size === 0) break;
      const chunkSource = String((await nextLoad(chunkUrl, { format: "module" })).source ?? "");
      const fromChunk = [...remaining].filter((name) => {
        const re = new RegExp("\\b" + name + "\\b[^\\n;]*\\bas\\b[\\s\\S]{0,200}?[,}]|export\\s*\\{[^}]*\\bas\\s+" + name + "\\b");
        return re.test(chunkSource);
      });
      if (fromChunk.length === 0) continue;
      const specifier = new URL(chunkUrl).pathname;
      suffix += "\nexport { " + fromChunk.map((name) => name + " as __mh_" + name).join(", ") + ' } from "' + specifier + '";\n';
      for (const name of fromChunk) remaining.delete(name);
    }
    if (remaining.size > 0) {
      throw new Error(
        "Unsupported Magic Context adapter: missing required exports: " +
          [...remaining].sort().join(", ")
      );
    }
  }
  return { ...result, source: source + suffix, shortCircuit: true };
}
