#!/usr/bin/env node
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { register } from "node:module";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { createInterface } from "node:readline";
import { pathToFileURL } from "node:url";

const CONTEXT_MARKER = "<!-- magic-hermes:context -->";
const SUPPORTED_SERIES = [0, 38];

function packageCandidates() {
  const candidates = [];
  if (process.env.MAGIC_CONTEXT_PACKAGE_ROOT) {
    candidates.push(process.env.MAGIC_CONTEXT_PACKAGE_ROOT);
  }
  const home = homedir();
  candidates.push(
    join(home, ".pi", "agent", "npm", "node_modules", "@cortexkit", "pi-magic-context"),
    join(home, ".config", "opencode", "node_modules", "@cortexkit", "pi-magic-context")
  );
  for (const base of [process.cwd(), dirname(process.argv[1] || process.cwd())]) {
    let current = resolve(base);
    for (;;) {
      candidates.push(join(current, "node_modules", "@cortexkit", "pi-magic-context"));
      const parent = dirname(current);
      if (parent === current) {
        break;
      }
      current = parent;
    }
  }
  return [...new Set(candidates)];
}

function locatePackage() {
  for (const root of packageCandidates()) {
    if (existsSync(join(root, "package.json")) && existsSync(join(root, "dist", "index.js"))) {
      return root;
    }
  }
  throw new Error(
    "@cortexkit/pi-magic-context was not found. Install Magic Context for Pi " +
    "or set MAGIC_CONTEXT_PACKAGE_ROOT to its package directory."
  );
}

function assertVersion(version) {
  const match = String(version).match(
    /^(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:[+][0-9A-Za-z.-]+)?$/
  );
  if (
    !match ||
    Number(match[1]) !== SUPPORTED_SERIES[0] ||
    Number(match[2]) !== SUPPORTED_SERIES[1]
  ) {
    throw new Error(
      "Magic Context " + version +
      " is unsupported; magic-hermes requires the 0.38.x series"
    );
  }
}

const packageRoot = locatePackage();
const packageJson = JSON.parse(readFileSync(join(packageRoot, "package.json"), "utf8"));
assertVersion(packageJson.version);
const adapterPath = join(packageRoot, "dist", "index.js");
process.env.MAGIC_HERMES_ADAPTER_URL = pathToFileURL(adapterPath).href;
register("./loader.mjs", import.meta.url);
const adapter = await import(process.env.MAGIC_HERMES_ADAPTER_URL + "?magic-hermes=1");
const mc = (name) => {
  const value = adapter["__mh_" + name];
  if (value === undefined) {
    throw new Error(
      "Magic Context " + packageJson.version + " does not expose required runtime symbol " + name
    );
  }
  return value;
};

const db = process.env.MAGIC_CONTEXT_DB_PATH
  ? mc("openDatabase")({ dbPath: process.env.MAGIC_CONTEXT_DB_PATH })
  : mc("openDatabase")();
if (!db) {
  throw new Error("Magic Context refused to open its shared context database");
}

const sessions = new Map();

function textContent(value) {
  if (typeof value === "string") {
    return value;
  }
  if (!Array.isArray(value)) {
    return value == null ? "" : JSON.stringify(value);
  }
  const parts = [];
  for (const item of value) {
    if (typeof item === "string") {
      parts.push(item);
    } else if (item && typeof item === "object" && item.type === "text") {
      parts.push(String(item.text || ""));
    } else if (item && typeof item === "object" && item.type === "image_url") {
      parts.push("[image]");
    }
  }
  return parts.join("\n");
}

function safeArguments(value) {
  if (value && typeof value === "object") {
    return value;
  }
  if (typeof value !== "string") {
    return {};
  }
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" ? parsed : { value };
  } catch {
    return { raw: value };
  }
}

function canonicalMessage(message) {
  const role = String(message?.role || "");
  const base = {
    role,
    content: textContent(message?.content)
  };
  if (role === "assistant") {
    base.tool_calls = Array.isArray(message.tool_calls)
      ? message.tool_calls.map((call) => ({
          id: String(call?.id || ""),
          name: String(call?.function?.name || call?.name || ""),
          arguments: call?.function?.arguments ?? call?.arguments ?? {}
        }))
      : [];
  }
  if (role === "tool") {
    base.tool_call_id = String(message.tool_call_id || "");
    base.name = String(message.name || "");
  }
  return base;
}

function signature(message) {
  return createHash("sha256")
    .update(JSON.stringify(canonicalMessage(message)))
    .digest("hex");
}

function toPiMessage(message) {
  const role = String(message.role || "");
  if (role === "user") {
    return { role: "user", content: textContent(message.content) };
  }
  if (role === "assistant") {
    const content = [];
    const text = textContent(message.content);
    if (text) {
      content.push({ type: "text", text });
    }
    for (const call of Array.isArray(message.tool_calls) ? message.tool_calls : []) {
      content.push({
        type: "toolCall",
        id: String(call?.id || ""),
        name: String(call?.function?.name || call?.name || ""),
        arguments: safeArguments(call?.function?.arguments ?? call?.arguments)
      });
    }
    return { role: "assistant", content };
  }
  if (role === "tool") {
    return {
      role: "toolResult",
      toolCallId: String(message.tool_call_id || ""),
      toolName: String(message.name || ""),
      content: [{ type: "text", text: textContent(message.content) }]
    };
  }
  return null;
}

function meaningfulMessages(messages) {
  const result = [];
  for (let inputIndex = 0; inputIndex < messages.length; inputIndex += 1) {
    const message = messages[inputIndex];
    if (!message || !["user", "assistant", "tool"].includes(message.role)) {
      continue;
    }
    const piMessage = toPiMessage(message);
    if (!piMessage) {
      continue;
    }
    result.push({
      inputIndex,
      source: message,
      piMessage,
      signature: signature(message)
    });
  }
  return result;
}

function deterministicId(sessionId, sig, occurrence) {
  return "mh-" + createHash("sha256")
    .update(sessionId + "\0" + sig + "\0" + occurrence)
    .digest("hex")
    .slice(0, 24);
}

function longestSuffixOverlap(existing, incoming) {
  const limit = Math.min(existing.length, incoming.length);
  for (let length = limit; length > 0; length -= 1) {
    let matches = true;
    const start = existing.length - length;
    for (let index = 0; index < length; index += 1) {
      if (existing[start + index].signature !== incoming[index].signature) {
        matches = false;
        break;
      }
    }
    if (matches) {
      return { length, existingStart: start };
    }
  }
  return { length: 0, existingStart: existing.length };
}

function buildEntryRecord(session, item, id) {
  return {
    id,
    signature: item.signature,
    message: item.piMessage,
    version: item.signature
  };
}

function assignEntries(session, messages) {
  const incoming = meaningfulMessages(messages);
  const existing = session.entries;
  const overlap = longestSuffixOverlap(existing, incoming);
  const assigned = [];
  const occurrenceCounts = new Map();

  for (const record of existing) {
    occurrenceCounts.set(
      record.signature,
      (occurrenceCounts.get(record.signature) || 0) + 1
    );
  }

  for (let index = 0; index < incoming.length; index += 1) {
    const item = incoming[index];
    let record;
    if (index < overlap.length) {
      record = existing[overlap.existingStart + index];
    } else {
      const occurrence = (occurrenceCounts.get(item.signature) || 0) + 1;
      occurrenceCounts.set(item.signature, occurrence);
      const id = deterministicId(session.id, item.signature, occurrence);
      record = buildEntryRecord(session, item, id);
      existing.push(record);
    }
    assigned.push({ ...item, id: record.id });
  }

  return assigned;
}

function rawMessagesFor(session) {
  const entries = session.entries.map((entry) => ({
    type: "message",
    id: entry.id,
    timestamp: entry.version,
    message: entry.message
  }));
  const raw = mc("convertEntriesToRawMessages")(entries);
  if (session.baseOrdinal > 0) {
    for (const message of raw) {
      message.ordinal += session.baseOrdinal;
    }
  }
  session.raw = raw;
  return raw;
}

function mapEntriesToRaw(session) {
  const mapped = new Map();
  const rawIds = new Map(session.raw.map((message) => [message.id, message.ordinal]));
  let pendingTools = [];

  for (const entry of session.entries) {
    const role = entry.message.role;
    if (role === "toolResult") {
      pendingTools.push(entry.id);
      continue;
    }
    if (role === "user") {
      const ordinal = rawIds.get(entry.id);
      if (ordinal !== undefined) {
        for (const pending of pendingTools) {
          mapped.set(pending, ordinal);
        }
        mapped.set(entry.id, ordinal);
      }
      pendingTools = [];
      continue;
    }
    if (role === "assistant") {
      if (pendingTools.length > 0) {
        const ordinal = rawIds.get("synth-user-" + pendingTools[0]);
        if (ordinal !== undefined) {
          for (const pending of pendingTools) {
            mapped.set(pending, ordinal);
          }
        }
        pendingTools = [];
      }
      const ordinal = rawIds.get(entry.id);
      if (ordinal !== undefined) {
        mapped.set(entry.id, ordinal);
      }
    }
  }
  if (pendingTools.length > 0) {
    const ordinal = rawIds.get("synth-user-" + pendingTools[0]);
    if (ordinal !== undefined) {
      for (const pending of pendingTools) {
        mapped.set(pending, ordinal);
      }
    }
  }
  return mapped;
}

function providerFor(session) {
  return {
    readMessages: () => session.raw,
    readMessagePage: (afterOrdinal, limit, finalWatermark) =>
      session.raw
        .filter((message) =>
          message.ordinal > afterOrdinal && message.ordinal <= finalWatermark
        )
        .slice(0, limit),
    getMessageCount: () =>
      session.raw.length === 0
        ? session.baseOrdinal
        : session.raw[session.raw.length - 1].ordinal
  };
}

function ingest(session, messages, index = true) {
  const assigned = assignEntries(session, messages);
  const raw = rawMessagesFor(session);
  if (index && raw.length > 0) {
    const finalWatermark = raw[raw.length - 1].ordinal;
    mc("indexMessagesAfterOrdinal")(db, session.id, raw, 0, finalWatermark);
  }
  const entryOrdinals = mapEntriesToRaw(session);
  const inputOrdinals = new Map();
  for (const item of assigned) {
    const ordinal = entryOrdinals.get(item.id);
    if (ordinal !== undefined) {
      inputOrdinals.set(item.inputIndex, ordinal);
    }
  }
  return { assigned, raw, inputOrdinals };
}

function curateTaskRunnable(config) {
  const schedule = config.dreamer?.tasks?.curate?.schedule;
  return (
    typeof schedule === "string" &&
    schedule.trim().length > 0 &&
    Boolean(mc("isDreamerRunnable")(config))
  );
}

function configSummary(config) {
  const model = String(config.historian?.model || "");
  return {
    enabled: Boolean(config.enabled),
    execute_threshold_percentage: Number(config.execute_threshold_percentage || 75),
    history_budget_percentage: Number(config.history_budget_percentage || 0.15),
    compaction_enabled: Boolean(config.compaction?.enabled),
    historian_timeout_ms: Number(config.historian_timeout_ms || 120000),
    historian_model: model,
    historian_two_pass: Boolean(config.historian?.two_pass),
    memory_enabled: Boolean(config.memory?.enabled),
    memory_budget_tokens: Number(config.memory?.injection_budget_tokens || 4000),
    memory_auto_promote: Boolean(config.memory?.auto_promote),
    dreamer_enabled: curateTaskRunnable(config),
    dreamer_model: String(config.dreamer?.model || model)
  };
}

async function captureTools(session) {
  const tools = new Map();
  const config = session.config;
  const pi = {
    registerTool(tool) {
      tools.set(tool.name, tool);
    },
    registerCommand() {}
  };
  mc("registerMagicContextTools")(pi, {
    db,
    ensureProjectRegistered: async () =>
      mc("ensureProjectRegisteredFromPiDirectory")(session.projectRoot, db),
    resolveProjectIdentity: () => session.projectIdentity,
    memoryEnabled: Boolean(config.memory?.enabled),
    embeddingEnabled: Boolean(config.embedding?.enabled),
    gitCommitsEnabled: Boolean(config.memory?.git_commit_indexing?.enabled),
    memoryToolEnabled: true,
    allowDreamerActions: true,
    // Hermes does not run the upstream smart-note evaluator, so the note tool
    // must not advertise surface_condition even when curate is enabled.
    dreamerEnabled: false,
    todowriteEnabled: false,
    compactionOff: !Boolean(config.compaction?.enabled)
  });

  const noteTool = tools.get("ctx_note");
  if (noteTool) {
    const parameters = noteTool.parameters || {};
    const properties = { ...(parameters.properties || {}) };
    delete properties.surface_condition;
    const required = Array.isArray(parameters.required)
      ? parameters.required.filter((name) => name !== "surface_condition")
      : parameters.required;
    let description = String(noteTool.description || "").replace(
      " Add surface_condition to make it a smart note (below).",
      ""
    );
    const smartNotesIndex = description.indexOf("\n\nSmart notes:");
    if (smartNotesIndex >= 0) {
      description = description.slice(0, smartNotesIndex);
    }
    tools.set("ctx_note", {
      ...noteTool,
      description,
      parameters: {
        ...parameters,
        properties,
        ...(required === undefined ? {} : { required })
      }
    });
  }

  session.tools = tools;
}

async function bind(args) {
  const sessionId = String(args.session_id || "");
  if (!sessionId) {
    throw new Error("session_id is required");
  }
  const projectRoot = resolve(String(args.project_root || process.cwd()));
  const loaded = mc("loadPiConfig")({ cwd: projectRoot });
  const config = loaded.config;
  if (!config.enabled) {
    throw new Error("Magic Context is disabled by the shared CortexKit config");
  }
  const projectIdentity = mc("resolveProjectIdentityForSession")(
    projectRoot,
    Boolean(config.allow_home_project)
  );
  if (!projectIdentity) {
    throw new Error(
      "Magic Context could not resolve a project identity for " + projectRoot
    );
  }

  let session = sessions.get(sessionId);
  if (!session) {
    const lastCompartmentEnd = mc("getLastCompartmentEndMessage")(db, sessionId);
    session = {
      id: sessionId,
      projectRoot,
      projectIdentity,
      config,
      entries: [],
      raw: [],
      baseOrdinal: Math.max(0, lastCompartmentEnd),
      tools: new Map(),
      pendingHistorian: null
    };
    sessions.set(sessionId, session);
  } else {
    session.projectRoot = projectRoot;
    session.projectIdentity = projectIdentity;
    session.config = config;
  }

  mc("recordSessionProjectIdentity")(db, sessionId, projectIdentity);
  await mc("ensureProjectRegisteredFromPiDirectory")(projectRoot, db);
  await captureTools(session);

  return {
    package_version: packageJson.version,
    project_identity: projectIdentity,
    config: configSummary(config),
    tool_schemas: [...session.tools.values()].map((tool) => ({
      name: tool.name,
      description: tool.description,
      parameters: tool.parameters
    }))
  };
}

function getSession(args) {
  const session = sessions.get(String(args.session_id || ""));
  if (!session) {
    throw new Error("Session is not bound; call bind first");
  }
  return session;
}

function resultText(result) {
  if (!result || !Array.isArray(result.content)) {
    return "";
  }
  return result.content
    .filter((part) => part && part.type === "text")
    .map((part) => String(part.text || ""))
    .join("\n");
}

async function executeTool(args) {
  const session = getSession(args);
  const messages = Array.isArray(args.messages) ? args.messages : [];
  if (messages.length > 0) {
    ingest(session, messages, true);
  } else {
    rawMessagesFor(session);
  }
  const tool = session.tools.get(String(args.name || ""));
  if (!tool) {
    throw new Error("Unknown Magic Context tool: " + String(args.name || ""));
  }
  const context = {
    cwd: session.projectRoot,
    sessionManager: { getSessionId: () => session.id }
  };
  let result = await mc("withRawMessageProvider")(
    session.id,
    providerFor(session),
    () => tool.execute(
      String(args.call_id || "magic-hermes"),
      args.arguments || {},
      undefined,
      () => {},
      context
    )
  );

  const expandText = resultText(result);
  const emptyExpand =
    args.name === "ctx_expand" &&
    (result?.isError || expandText.startsWith("No messages found in range"));
  if (emptyExpand) {
    const start = Number(args.arguments?.start);
    const end = Number(args.arguments?.end);
    if (Number.isFinite(start) && Number.isFinite(end)) {
      const fallback = mc("buildCanonicalChunkTextFromFts")(
        db,
        session.id,
        start,
        end
      );
      if (fallback) {
        result = { content: [{ type: "text", text: fallback }] };
      }
    }
  }
  return {
    text: resultText(result),
    is_error: Boolean(result?.isError),
    result
  };
}

function compactView(session, messages, inputOrdinals, historyBudgetTokens) {
  const compartments = mc("getCompartments")(db, session.id);
  if (compartments.length === 0) {
    return { messages, history: "", last_compartment_end: -1 };
  }
  const history = mc("renderDecayedCompartments")({
    compartments,
    historyBudgetTokens: Math.max(0, Number(historyBudgetTokens || 0))
  });
  if (!history) {
    return { messages, history: "", last_compartment_end: -1 };
  }
  const lastEnd = mc("getLastCompartmentEndMessage")(db, session.id);
  const kept = [];
  for (let index = 0; index < messages.length; index += 1) {
    const message = messages[index];
    if (
      message?.role === "system" &&
      typeof message.content === "string" &&
      message.content.includes(CONTEXT_MARKER)
    ) {
      continue;
    }
    const ordinal = inputOrdinals.get(index);
    if (ordinal !== undefined && ordinal <= lastEnd) {
      continue;
    }
    kept.push(message);
  }

  let insertAt = 0;
  while (
    insertAt < kept.length &&
    ["system", "developer"].includes(String(kept[insertAt]?.role || ""))
  ) {
    insertAt += 1;
  }
  const contextMessage = {
    role: "system",
    content:
      CONTEXT_MARKER + "\n<session-history>\n" + history + "\n</session-history>"
  };
  const compacted = [
    ...kept.slice(0, insertAt),
    contextMessage,
    ...kept.slice(insertAt)
  ];
  return {
    messages: compacted,
    history,
    last_compartment_end: lastEnd
  };
}

function observe(args) {
  const session = getSession(args);
  const messages = Array.isArray(args.messages) ? args.messages : [];
  const ingested = ingest(session, messages, true);
  return {
    entry_count: session.entries.length,
    raw_message_count: ingested.raw.length,
    last_indexed_ordinal: mc("getLastIndexedOrdinal")(db, session.id)
  };
}

function renderContext(args) {
  const session = getSession(args);
  const messages = Array.isArray(args.messages) ? args.messages : [];
  const ingested = ingest(session, messages, true);
  return compactView(
    session,
    messages,
    ingested.inputOrdinals,
    Number(args.history_budget_tokens || 0)
  );
}

function historianPrepare(args) {
  const session = getSession(args);
  const messages = Array.isArray(args.messages) ? args.messages : [];
  const ingested = ingest(session, messages, true);
  const priorCompartments = mc("getCompartments")(db, session.id);
  const lastEnd = mc("getLastCompartmentEndMessage")(db, session.id);
  const offset = Math.max(1, lastEnd + 1);
  const rawEnd = ingested.raw.length > 0
    ? ingested.raw[ingested.raw.length - 1].ordinal
    : session.baseOrdinal;
  const protectLast = Math.max(0, Number(args.protect_last_n ?? 6));
  const protectedTailStart = Math.max(offset, rawEnd - protectLast + 1);
  if (protectedTailStart <= offset) {
    session.pendingHistorian = null;
    return { ready: false, reason: "protected-tail" };
  }

  const model = String(session.config.historian?.model || "");
  const contextLimit = mc("resolveHistorianContextLimit")(model);
  const chunkTokens = mc("deriveHistorianChunkTokens")(contextLimit);
  const chunk = mc("withRawMessageProvider")(
    session.id,
    providerFor(session),
    () => mc("readSessionChunk")(
      session.id,
      chunkTokens,
      offset,
      protectedTailStart
    )
  );
  if (!chunk.text || chunk.messageCount === 0) {
    session.pendingHistorian = null;
    return { ready: false, reason: "empty-chunk" };
  }

  const memories = mc("getMemoriesByProject")(
    db,
    session.projectIdentity,
    ["active", "permanent"]
  );
  const selected = mc("trimMemoriesToBudgetV2")(
    session.id,
    memories,
    Number(session.config.memory?.injection_budget_tokens || 4000)
  ).renderOrder;
  const projectMemory = mc("renderMemoryBlockV2")(selected);
  const references = mc("buildReferenceBlocks")({
    sessionId: session.id,
    chunkStart: chunk.startIndex,
    sessionCompartments: priorCompartments
  });
  const prompt = mc("buildCompartmentAgentPrompt")({
    seedExamples: references.seedExamples,
    sessionReferences: references.sessionReferences,
    projectMemory,
    inputSource: chunk.text,
    memoryEnabled: Boolean(session.config.memory?.enabled)
  });
  const systemPrompt = mc("withContentLanguageDirective")(
    mc("COMPARTMENT_AGENT_SYSTEM_PROMPT"),
    session.config.language,
    { preserveUserQuotes: true }
  );
  const editorSystemPrompt = mc("withContentLanguageDirective")(
    mc("HISTORIAN_EDITOR_SYSTEM_PROMPT"),
    session.config.language,
    { preserveUserQuotes: true }
  );

  session.pendingHistorian = {
    chunk,
    priorCompartments,
    inputMessages: messages,
    inputOrdinals: ingested.inputOrdinals,
    historyBudgetTokens: Number(args.history_budget_tokens || 0),
    prompt,
    systemPrompt,
    editorSystemPrompt,
    validatedDraft: null
  };
  return {
    ready: true,
    system_prompt: systemPrompt,
    prompt,
    chunk: {
      start: chunk.startIndex,
      end: chunk.endIndex,
      message_count: chunk.messageCount,
      token_estimate: chunk.tokenEstimate
    },
    model,
    two_pass: Boolean(session.config.historian?.two_pass),
    timeout_ms: Number(session.config.historian_timeout_ms || 120000)
  };
}

function historianPublish(args) {
  const session = getSession(args);
  const pending = session.pendingHistorian;
  if (!pending) {
    throw new Error("No historian pass is pending");
  }
  const output = String(args.output || "");
  let validation = mc("validateHistorianOutput")(
    output,
    session.id,
    pending.chunk,
    pending.priorCompartments,
    pending.priorCompartments.length
  );
  const editorPass = args.editor_pass === true;
  if (editorPass && !validation.ok && pending.validatedDraft?.ok) {
    validation = pending.validatedDraft;
  } else if (!validation.ok) {
    return {
      ok: false,
      error: String(validation.error || "invalid historian output"),
      repair_prompt: mc("buildHistorianRepairPrompt")(
        pending.prompt,
        output,
        String(validation.error || "invalid historian output"),
        session.config.language
      ),
      system_prompt: pending.systemPrompt
    };
  }

  if (session.config.historian?.two_pass && !editorPass) {
    pending.validatedDraft = validation;
    return {
      ok: false,
      needs_editor: true,
      editor_system_prompt: pending.editorSystemPrompt,
      editor_prompt: mc("buildHistorianEditorPrompt")(output)
    };
  }

  const compartments = validation.compartments;
  const publishableEvents = (validation.events || []).filter((event) =>
    typeof event.atCompartment !== "number" ||
    event.atCompartment <= compartments.length
  );
  let promoted = [];
  let persistedIds = [];
  let eventsStored = 0;
  let published = false;
  db.exec("BEGIN IMMEDIATE");
  try {
    mc("appendCompartments")(db, session.id, compartments);
    persistedIds = mc("getCompartments")(db, session.id)
      .slice(-compartments.length)
      .map((compartment) => compartment.id);
    if (session.config.memory?.enabled && session.config.memory?.auto_promote) {
      promoted = mc("promoteSessionFactsDurable")(
        db,
        session.id,
        session.projectIdentity,
        validation.facts || []
      );
    }
    if (publishableEvents.length > 0) {
      try {
        mc("insertCompartmentEvents")(
          db,
          session.id,
          publishableEvents,
          persistedIds
        );
        eventsStored = publishableEvents.length;
      } catch {
        // Event storage is an optional historian side channel upstream as well.
      }
    }
    db.exec("COMMIT");
    published = true;
  } finally {
    if (!published) {
      try {
        db.exec("ROLLBACK");
      } catch {
        // Preserve the original publication error.
      }
    }
  }

  let userCandidatesStored = 0;
  if (
    mc("userMemoryCollectionEnabled")(session.config.dreamer) &&
    validation.userObservations?.length
  ) {
    try {
      mc("insertUserMemoryCandidates")(
        db,
        validation.userObservations.map((observation) => ({
          content: observation,
          sessionId: session.id,
          sourceCompartmentStart: compartments[0]?.startMessage,
          sourceCompartmentEnd: compartments.at(-1)?.endMessage
        }))
      );
      userCandidatesStored = validation.userObservations.length;
    } catch {
      // Collection is experimental upstream and must not block compaction.
    }
  }
  const view = compactView(
    session,
    pending.inputMessages,
    pending.inputOrdinals,
    pending.historyBudgetTokens
  );
  session.pendingHistorian = null;
  return {
    ok: true,
    compartments_added: compartments.length,
    memories_promoted: promoted.length,
    events_stored: eventsStored,
    user_memory_candidates_stored: userCandidatesStored,
    ...view
  };
}

function memoryContext(args) {
  const session = getSession(args);
  const memories = mc("getMemoriesByProject")(
    db,
    session.projectIdentity,
    ["active", "permanent"]
  );
  const budget = Number(
    args.budget_tokens || session.config.memory?.injection_budget_tokens || 4000
  );
  const selected = mc("trimMemoriesToBudgetV2")(
    session.id,
    memories,
    budget
  ).renderOrder;
  return {
    text: mc("renderMemoryBlockV2")(selected),
    count: selected.length
  };
}

function dreamerPrepare(args) {
  const session = getSession(args);
  if (!curateTaskRunnable(session.config)) {
    return { ready: false, reason: "curate-disabled" };
  }
  const memories = mc("getMemoriesByProject")(
    db,
    session.projectIdentity,
    ["active", "permanent"]
  );
  if (memories.length < 2) {
    return { ready: false, reason: "insufficient-memories" };
  }
  const prompt = mc("buildDreamTaskPrompt")("curate", {
    projectPath: session.projectIdentity,
    curate: { memories },
    userMemories: []
  });
  const systemPrompt = mc("withContentLanguageDirective")(
    mc("CURATE_SYSTEM_PROMPT"),
    session.config.language
  );
  return {
    ready: true,
    system_prompt: systemPrompt,
    prompt:
      prompt +
      "\n\nDo not call tools in this single-pass Hermes review. Return JSON only: " +
      '{"operations":[{"action":"update|merge|archive","ids":[1],"content":"...",' +
      '"category":"PROJECT_RULES","reason":"..."}]}. ' +
      "Use an empty operations array when no safe quality improvement is needed.",
    model: String(session.config.dreamer?.model || session.config.historian?.model || "")
  };
}

async function dreamerApply(args) {
  const session = getSession(args);
  const operations = Array.isArray(args.operations) ? args.operations : [];
  const allowed = new Set(["update", "merge", "archive"]);
  const projectMemoryIds = new Set(
    mc("getMemoriesByProject")(
      db,
      session.projectIdentity,
      ["active", "permanent"]
    ).map((memory) => Number(memory.id))
  );
  const results = [];
  for (const operation of operations.slice(0, 20)) {
    if (!allowed.has(operation?.action) || !Array.isArray(operation?.ids)) {
      continue;
    }
    const ids = operation.ids
      .filter((id) => Number.isFinite(id) && projectMemoryIds.has(id))
      .slice(0, 20);
    if (ids.length === 0) {
      continue;
    }
    const toolArgs = {
      action: operation.action,
      ids
    };
    if (typeof operation.content === "string") {
      toolArgs.content = operation.content;
    }
    if (typeof operation.category === "string") {
      toolArgs.category = operation.category;
    }
    if (typeof operation.reason === "string") {
      toolArgs.reason = operation.reason;
    }
    const result = await executeTool({
      session_id: session.id,
      name: "ctx_memory",
      arguments: toolArgs,
      call_id: "magic-hermes-dreamer"
    });
    results.push(result);
  }
  return { applied: results.length, results };
}

const methods = {
  hello: async () => ({
    package_root: packageRoot,
    package_version: packageJson.version,
    harness: "hermes"
  }),
  bind,
  observe: async (args) => observe(args),
  render_context: async (args) => renderContext(args),
  tool: executeTool,
  historian_prepare: async (args) => historianPrepare(args),
  historian_publish: async (args) => historianPublish(args),
  memory_context: async (args) => memoryContext(args),
  dreamer_prepare: async (args) => dreamerPrepare(args),
  dreamer_apply: dreamerApply,
  close: async () => {
    mc("closeQuietly")(db);
    return { closed: true };
  }
};

const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of lines) {
  if (!line.trim()) {
    continue;
  }
  let request;
  try {
    request = JSON.parse(line);
    const method = methods[request.method];
    if (!method) {
      throw new Error("Unknown runtime method: " + String(request.method || ""));
    }
    const result = await method(request.params || {});
    process.stdout.write(JSON.stringify({ id: request.id, result }) + "\n");
  } catch (error) {
    process.stdout.write(JSON.stringify({
      id: request?.id ?? null,
      error: {
        message: error instanceof Error ? error.message : String(error),
        stack: process.env.MAGIC_HERMES_DEBUG && error instanceof Error
          ? error.stack
          : undefined
      }
    }) + "\n");
  }
}
