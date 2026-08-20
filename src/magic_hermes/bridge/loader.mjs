const EXPOSED = [
  "openDatabase",
  "closeQuietly",
  "loadPiConfig",
  "ensureProjectRegisteredFromPiDirectory",
  "resolveProjectIdentityForSession",
  "recordSessionProjectIdentity",
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
  const piHarness = 'setHarness("pi");';
  if (!source.includes(piHarness)) {
    throw new Error("Unsupported Magic Context adapter: Pi harness initializer not found");
  }
  source = source.replace(piHarness, 'setHarness("hermes");');

  const suffix = "\nexport { " + EXPOSED.map(
    (name) => name + " as __mh_" + name
  ).join(", ") + " };\n";
  return { ...result, source: source + suffix, shortCircuit: true };
}
