#!/usr/bin/env node
import { createHash, randomUUID } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { register } from "node:module";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { createInterface } from "node:readline";
import { pathToFileURL } from "node:url";

const CONTEXT_MARKER = "<!-- magic-hermes:context -->";
const compat = JSON.parse(
  readFileSync(new URL("../magic_context_compat.json", import.meta.url), "utf8")
);
const SUPPORTED_SERIES = compat.supported_series;
const TESTED_VERSION = compat.tested_version;
if (
  !Array.isArray(SUPPORTED_SERIES) ||
  SUPPORTED_SERIES.length !== 2 ||
  !SUPPORTED_SERIES.every((part) => Number.isInteger(part) && part >= 0) ||
  typeof TESTED_VERSION !== "string"
) {
  throw new Error("Invalid magic-hermes Magic Context compatibility manifest");
}

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
      " is unsupported; magic-hermes requires the " +
      SUPPORTED_SERIES.join(".") + ".x series (validated with " + TESTED_VERSION + ")"
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

const CORE_REQUIRED_SYMBOLS = [
  "openDatabase",
  "loadPiConfig",
  "resolveProjectIdentityForSession",
  "ensureProjectRegisteredFromPiDirectory",
  "registerMagicContextTools",
  "convertEntriesToRawMessages",
  "indexMessagesAfterOrdinal",
  "createScheduler",
  "createTagger",
  "createPiTranscript",
  "tagTranscript",
  "getPendingOps",
  "applyPendingOperations",
  "resolveHistorianFromConfig",
  "checkCompartmentTrigger",
  "acquireCompartmentLease",
  "releaseCompartmentLease"
];

function missingSymbols(names) {
  return names.filter((name) => adapter["__mh_" + name] === undefined);
}

const missingCoreSymbols = missingSymbols(CORE_REQUIRED_SYMBOLS);
if (missingCoreSymbols.length > 0) {
  throw new Error(
    "Magic Context " + packageJson.version +
    " is missing required magic-hermes runtime symbols: " +
    missingCoreSymbols.join(", ")
  );
}

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

function normalizeTimestampMs(value) {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value > 1e11 ? value : value * 1000;
  }
  if (typeof value !== "string" || !value.trim()) {
    return undefined;
  }
  const trimmed = value.trim();
  if (/^[0-9]+(?:[.][0-9]+)?$/.test(trimmed)) {
    const numeric = Number(trimmed);
    if (Number.isFinite(numeric)) {
      return numeric > 1e11 ? numeric : numeric * 1000;
    }
  }
  const parsed = Date.parse(trimmed);
  return Number.isFinite(parsed) ? parsed : undefined;
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
  const timestamp = normalizeTimestampMs(message.timestamp);
  const temporal = timestamp === undefined ? {} : { timestamp };
  if (role === "user") {
    return { role: "user", content: textContent(message.content), ...temporal };
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
    return { role: "assistant", content, ...temporal };
  }
  if (role === "tool") {
    return {
      role: "toolResult",
      toolCallId: String(message.tool_call_id || ""),
      toolName: String(message.name || ""),
      content: [{ type: "text", text: textContent(message.content) }],
      ...temporal
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

function maybeRebaseRestoredFullSnapshot(session, incoming) {
  if (session.entries.length > 0 || session.baseOrdinal <= 0 || incoming.length === 0) {
    return;
  }
  // A fresh sidecar cannot know whether Hermes restored the complete session or
  // supplied only the post-compartment tail.  The upstream source table already
  // persists our deterministic entry id and ordinal.  If the first incoming
  // entry is the same source that MC knows at ordinal 1, this is a full restore
  // and must start at zero rather than offsetting the transcript a second time.
  const first = incoming[0];
  const firstId = deterministicId(session.id, first.signature, 1);
  const row = db.prepare(
    "SELECT message_ordinal FROM message_history_source WHERE session_id = ? AND message_id = ?"
  ).get(session.id, firstId);
  if (Number(row?.message_ordinal) === 1) {
    session.baseOrdinal = 0;
  }
}

function commonPrefixLength(existing, incoming) {
  const limit = Math.min(existing.length, incoming.length);
  let length = 0;
  while (
    length < limit &&
    existing[length].signature === incoming[length].signature
  ) {
    length += 1;
  }
  return length;
}

function tableExists(name) {
  return Boolean(
    db.prepare("SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name = ? LIMIT 1")
      .get(name)
  );
}

function truncateMessageHistoryFrom(sessionId, floorOrdinal) {
  const floor = Math.max(1, Math.floor(floorOrdinal));
  db.transaction(() => {
    if (tableExists("message_history_fts")) {
      db.prepare(
        "DELETE FROM message_history_fts WHERE session_id = ? AND CAST(message_ordinal AS INTEGER) >= ?"
      ).run(sessionId, floor);
    }
    if (tableExists("message_history_source")) {
      db.prepare(
        "DELETE FROM message_history_source WHERE session_id = ? AND message_ordinal >= ?"
      ).run(sessionId, floor);
    }
    if (tableExists("message_history_index")) {
      db.prepare(
        `UPDATE message_history_index
            SET last_indexed_ordinal = MIN(last_indexed_ordinal, ?),
                dirty_floor_ordinal = 0,
                updated_at = ?
          WHERE session_id = ?`
      ).run(floor - 1, Date.now(), sessionId);
    }
    if (tableExists("compression_depth")) {
      db.prepare(
        "DELETE FROM compression_depth WHERE session_id = ? AND message_ordinal >= ?"
      ).run(sessionId, floor);
    }
  })();
}

function deleteRemovedTagState(sessionId, removedEntries) {
  const messageIds = new Set();
  const callIds = new Set();
  for (const entry of removedEntries) {
    messageIds.add(String(entry.id || ""));
    const message = entry.message || {};
    if (message.role === "assistant" && Array.isArray(message.content)) {
      for (const part of message.content) {
        if (part?.type === "toolCall" && part.id) {
          callIds.add(String(part.id));
        }
      }
    } else if (message.role === "toolResult" && message.toolCallId) {
      callIds.add(String(message.toolCallId));
    }
  }
  messageIds.delete("");
  callIds.delete("");
  if (!tableExists("tags") || (messageIds.size === 0 && callIds.size === 0)) {
    return;
  }
  const ids = [...messageIds];
  const calls = [...callIds];
  const clauses = [];
  const params = [sessionId];
  if (ids.length > 0) {
    const q = ids.map(() => "?").join(",");
    clauses.push(`message_id IN (${q})`, `tool_owner_message_id IN (${q})`);
    params.push(...ids, ...ids);
  }
  if (calls.length > 0) {
    clauses.push(`message_id IN (${calls.map(() => "?").join(",")})`);
    params.push(...calls);
  }
  const rows = db.prepare(
    `SELECT tag_number FROM tags WHERE session_id = ? AND (${clauses.join(" OR ")})`
  ).all(...params);
  db.transaction(() => {
    for (const row of rows) {
      const tag = Number(row.tag_number);
      if (!Number.isFinite(tag)) {
        continue;
      }
      if (tableExists("source_contents")) {
        db.prepare(
          "DELETE FROM source_contents WHERE session_id = ? AND tag_id = ?"
        ).run(sessionId, tag);
      }
      if (tableExists("pending_ops")) {
        db.prepare(
          "DELETE FROM pending_ops WHERE session_id = ? AND tag_id = ?"
        ).run(sessionId, tag);
      }
      db.prepare(
        "DELETE FROM tags WHERE session_id = ? AND tag_number = ?"
      ).run(sessionId, tag);
    }
  })();
}

function resetSessionDerivedState(session) {
  // This is the host-side equivalent of upstream clearSession() for a branch
  // rewind that invalidates already-published compartments.  The list mirrors
  // MC's session-scoped tables; project-wide memories/notes/schedules are not
  // guessed or rolled back by the connector.
  const sessionTables = [
    "pending_ops",
    "source_contents",
    "tool_owner_backfill_state",
    "tags",
    "session_meta",
    "compartment_chunk_embeddings",
    "compartments",
    "compression_depth",
    "session_facts",
    "compartment_state_lease",
    "recomp_compartments",
    "recomp_facts",
    "user_memory_candidates",
    "primer_candidates",
    "m0_mutation_log",
    "compartment_events",
    "subagent_invocations",
    "historian_runs",
    "plugin_messages",
    "transform_decisions",
    "synapse_batch_ledger",
    "embedding_measurement_corpus",
    "pending_session_cleanup",
    "message_history_fts",
    "message_history_source",
    "message_history_index"
  ];
  db.transaction(() => {
    for (const table of sessionTables) {
      if (!tableExists(table)) {
        continue;
      }
      db.prepare(`DELETE FROM ${table} WHERE session_id = ?`).run(session.id);
    }
    if (tableExists("notes")) {
      db.prepare(
        "DELETE FROM notes WHERE session_id = ? AND type = 'session'"
      ).run(session.id);
    }
  })();
  mc("recordSessionProjectIdentity")(db, session.id, session.projectIdentity);
  session.entries = [];
  session.raw = [];
  session.baseOrdinal = 0;
  session.pendingHistorian = null;
  session.tagger = mc("createTagger")();
  session.scheduler = null;
  session.schedulerConfigKey = "";
  session.lastUsage = { percentage: 0, inputTokens: 0 };
  syncSessionPolicy(session);
}

function reconcileBranchSnapshot(session, incoming) {
  const existing = session.entries;
  if (existing.length === 0) {
    return 0;
  }
  const prefix = commonPrefixLength(existing, incoming);
  if (prefix === existing.length && incoming.length >= existing.length) {
    return prefix;
  }
  if (prefix === existing.length && incoming.length === existing.length) {
    return prefix;
  }

  rawMessagesFor(session);
  const oldOrdinals = mapEntriesToRaw(session);
  const removed = existing.slice(prefix);
  let floor = Infinity;
  for (const entry of removed) {
    const ordinal = oldOrdinals.get(entry.id);
    if (Number.isFinite(ordinal)) {
      floor = Math.min(floor, ordinal);
    }
  }
  if (!Number.isFinite(floor)) {
    floor = session.baseOrdinal + prefix + 1;
  }
  const lastCompartmentEnd = mc("getLastCompartmentEndMessage")(db, session.id);
  if (lastCompartmentEnd >= floor) {
    resetSessionDerivedState(session);
    return 0;
  }

  deleteRemovedTagState(session.id, removed);
  truncateMessageHistoryFrom(session.id, floor);
  existing.splice(prefix);
  session.raw = [];
  session.pendingHistorian = null;
  session.tagger = mc("createTagger")();
  return prefix;
}

function buildEntryRecord(session, item, id) {
  // Hermes transcripts do not guarantee a timestamp field on every message.
  // Capture first-observation time once so MC's durable source_version remains
  // useful for retrospective ordering across process restarts.
  const timestamp = normalizeTimestampMs(item.source?.timestamp) ?? Date.now();
  return {
    id,
    signature: item.signature,
    message: item.piMessage,
    timestamp,
    // Pi SessionEntry.version is the historian's source-time hint. Preserve the
    // host timestamp when Hermes has one; identity matching uses signature.
    version: timestamp ?? item.signature
  };
}

function assignEntries(session, messages) {
  const incoming = meaningfulMessages(messages);
  maybeRebaseRestoredFullSnapshot(session, incoming);
  const matchedPrefix = reconcileBranchSnapshot(session, incoming);
  const existing = session.entries;
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
    if (index < matchedPrefix && index < existing.length) {
      record = existing[index];
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
    version: entry.version,
    ...(entry.timestamp === undefined ? {} : { timestamp: entry.timestamp }),
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
    readMessageById: (messageId) =>
      session.entries.find((entry) => entry.id === messageId) ?? null,
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

function canonicalModelKey(provider, model) {
  const providerRef = String(provider || "").trim();
  const modelRef = String(model || "").trim();
  if (!modelRef) {
    return "";
  }
  if (modelRef.includes("/")) {
    return modelRef;
  }
  if (providerRef && providerRef !== "auto") {
    return providerRef + "/" + modelRef;
  }
  return modelRef;
}

function schedulerForSession(session) {
  const key = JSON.stringify([
    session.config.execute_threshold_percentage,
    session.config.execute_threshold_tokens ?? null
  ]);
  if (!session.scheduler || session.schedulerConfigKey !== key) {
    session.scheduler = mc("createScheduler")({
      executeThresholdPercentage: session.config.execute_threshold_percentage,
      executeThresholdTokens: session.config.execute_threshold_tokens
    });
    session.schedulerConfigKey = key;
  }
  return session.scheduler;
}

function syncSessionPolicy(session) {
  const meta = mc("getOrCreateSessionMeta")(db, session.id);
  const modelKey = session.modelKey || meta.lastObservedModelKey || "";
  const cacheTtl = mc("resolveCacheTtl")(session.config.cache_ttl, modelKey);
  const updates = { cacheTtl };
  if (modelKey) {
    updates.lastObservedModelKey = modelKey;
  }
  if (session.contextLimit > 0) {
    updates.lastUsageContextLimit = session.contextLimit;
  }
  mc("updateSessionMeta")(db, session.id, updates);
  session.modelKey = modelKey;
  session.lastUsage = {
    percentage: Number(meta.lastContextPercentage || 0),
    inputTokens: Number(meta.lastInputTokens || 0)
  };
  if (!session.contextLimit && Number(meta.lastUsageContextLimit || 0) > 0) {
    session.contextLimit = Number(meta.lastUsageContextLimit);
  }
  return mc("getOrCreateSessionMeta")(db, session.id);
}

function updateModel(args) {
  const session = getSession(args);
  session.contextLimit = Math.max(0, Number(args.context_length || 0));
  session.provider = String(args.provider || "");
  session.modelKey = canonicalModelKey(args.provider, args.model);
  const cacheTtl = mc("resolveCacheTtl")(
    session.config.cache_ttl,
    session.modelKey
  );
  mc("updateSessionMeta")(db, session.id, {
    cacheTtl,
    lastObservedModelKey: session.modelKey || null,
    lastUsageContextLimit: session.contextLimit || null
  });
  return {
    model_key: session.modelKey,
    context_limit: session.contextLimit,
    cache_ttl: cacheTtl
  };
}

function updateUsage(args) {
  const session = getSession(args);
  const inputTokens = Math.max(0, Number(args.input_tokens || 0));
  const contextLimit = Math.max(
    0,
    Number(args.context_length || session.contextLimit || 0)
  );
  if (contextLimit > 0) {
    session.contextLimit = contextLimit;
  }
  const percentage = contextLimit > 0 ? inputTokens / contextLimit * 100 : 0;
  session.lastUsage = { percentage, inputTokens };
  const meta = mc("getOrCreateSessionMeta")(db, session.id);
  const updates = {
    lastResponseTime: Date.now(),
    lastContextPercentage: percentage,
    lastInputTokens: inputTokens,
    observedSafeInputTokens: Math.max(
      Number(meta.observedSafeInputTokens || 0),
      inputTokens
    )
  };
  if (contextLimit > 0) {
    updates.lastUsageContextLimit = contextLimit;
  }
  mc("updateSessionMeta")(db, session.id, updates);
  return { percentage, input_tokens: inputTokens, context_limit: contextLimit };
}

function schedulerDecision(session) {
  if (!session.config.compaction?.enabled) {
    return "disabled";
  }
  const meta = mc("getOrCreateSessionMeta")(db, session.id);
  return schedulerForSession(session).shouldExecute(
    meta,
    session.lastUsage,
    Date.now(),
    session.id,
    session.modelKey || undefined,
    session.contextLimit || undefined
  );
}

function fromPiMessage(original, message) {
  const output = structuredClone(original);
  if (!message || typeof message !== "object") {
    return output;
  }
  if (message.role === "user") {
    output.content = textContent(message.content);
    return output;
  }
  if (message.role === "assistant") {
    const parts = Array.isArray(message.content) ? message.content : [];
    output.content = parts
      .filter((part) => part?.type === "text")
      .map((part) => String(part.text || ""))
      .join("\n");
    output.tool_calls = parts
      .filter((part) => part?.type === "toolCall")
      .map((part) => ({
        id: String(part.id || ""),
        type: "function",
        function: {
          name: String(part.name || ""),
          arguments: typeof part.arguments === "string"
            ? part.arguments
            : JSON.stringify(part.arguments || {})
        }
      }));
    return output;
  }
  if (message.role === "toolResult") {
    output.content = textContent(message.content);
  }
  return output;
}

function applyMagicContextTransform(session, messages, options = {}) {
  const ingested = options.ingested || ingest(session, messages, true);
  if (ingested.assigned.length === 0) {
    return {
      messages: [...messages],
      ingested,
      scheduler_decision: schedulerDecision(session),
      pending_ops: 0,
      mutated: false
    };
  }
  session.tagger = session.tagger || mc("createTagger")();
  const piMessages = ingested.assigned.map((item) => structuredClone(item.piMessage));
  if (session.config.temporal_awareness === true) {
    mc("injectPiTemporalMarkers")(piMessages);
  }
  const entryIds = ingested.assigned.map((item) => item.id);
  const transcript = mc("createPiTranscript")(piMessages, session.id, entryIds);
  const tagged = mc("tagTranscript")(
    session.id,
    transcript,
    session.tagger,
    db,
    { skipPrefixInjection: options.inject_tags === false }
  );
  let mutated = Boolean(
    mc("applyFlushedStatuses")(session.id, db, tagged.targets)
  );
  const decision = schedulerDecision(session);
  let pending = mc("getPendingOps")(db, session.id);
  if (
    options.apply_pending !== false &&
    decision === "execute" &&
    pending.length > 0
  ) {
    mutated = Boolean(
      mc("applyPendingOperations")(
        session.id,
        db,
        tagged.targets,
        Number(session.config.protected_tags || 20)
      )
    ) || mutated;
    pending = mc("getPendingOps")(db, session.id);
  }
  transcript.commit();
  const transformed = transcript.getOutputMessages();
  const output = messages.map((message) => structuredClone(message));
  for (let index = 0; index < ingested.assigned.length; index += 1) {
    const assigned = ingested.assigned[index];
    output[assigned.inputIndex] = fromPiMessage(
      assigned.source,
      transformed[index]
    );
  }
  return {
    messages: output,
    ingested,
    scheduler_decision: decision,
    pending_ops: pending.length,
    mutated
  };
}

function pressureState(args) {
  const session = getSession(args);
  if (!session.config.compaction?.enabled) {
    return {
      enabled: false,
      should_block: false,
      percentage: 0,
      execute_threshold_percentage: 0,
      emergency_percentage: 95
    };
  }
  const historian = mc("resolveHistorianFromConfig")(session.config);
  if (!historian) {
    return {
      enabled: false,
      should_block: false,
      percentage: 0,
      execute_threshold_percentage: 0,
      emergency_percentage: 95
    };
  }
  const contextLimit = Math.max(
    0,
    Number(args.context_length || session.contextLimit || 0)
  );
  const inputTokens = Math.max(
    0,
    Number(args.input_tokens ?? session.lastUsage.inputTokens ?? 0)
  );
  const inputs = mc("resolvePiHistorianTriggerInputs")({
    historian,
    modelKey: session.modelKey || undefined,
    usageContextLimit: contextLimit || undefined,
    sessionId: session.id
  });
  const bands = mc("escalationBands")(inputs.executeThresholdPercentage);
  const percentage = contextLimit > 0 ? inputTokens / contextLimit * 100 : 0;
  return {
    enabled: true,
    should_block: percentage >= bands.emergencyPercentage,
    percentage,
    execute_threshold_percentage: inputs.executeThresholdPercentage,
    force_materialization_percentage: bands.forceMaterializationPercentage,
    emergency_percentage: bands.emergencyPercentage,
    context_limit: contextLimit,
    input_tokens: inputTokens
  };
}

function historianDecision(args) {
  const session = getSession(args);
  if (!session.config.compaction?.enabled) {
    return { should_fire: false, reason: "compaction-disabled" };
  }
  const historian = mc("resolveHistorianFromConfig")(session.config);
  if (!historian) {
    return { should_fire: false, reason: "historian-disabled" };
  }
  const messages = Array.isArray(args.messages) ? args.messages : [];
  const transformed = applyMagicContextTransform(session, messages, {
    inject_tags: false,
    apply_pending: false
  });
  const inputs = mc("resolvePiHistorianTriggerInputs")({
    historian,
    modelKey: session.modelKey || undefined,
    usageContextLimit: session.contextLimit || undefined,
    sessionId: session.id
  });
  const meta = mc("getOrCreateSessionMeta")(db, session.id);
  const trigger = mc("withRawMessageProvider")(
    session.id,
    providerFor(session),
    () => mc("checkCompartmentTrigger")(
      db,
      session.id,
      meta,
      session.lastUsage,
      0,
      inputs.executeThresholdPercentage,
      inputs.triggerBudget,
      inputs.clearReasoningAge,
      inputs.commitClusterTrigger,
      mc("getActiveTagsBySession")(db, session.id),
      inputs.contextLimit,
      undefined,
      undefined,
      undefined
    )
  );
  return {
    should_fire: Boolean(trigger?.shouldFire),
    reason: String(trigger?.reason || "not-due"),
    boundary_snapshot: trigger?.boundarySnapshot || null,
    execute_threshold_percentage: inputs.executeThresholdPercentage,
    trigger_budget: inputs.triggerBudget,
    context_limit: inputs.contextLimit,
    scheduler_decision: transformed.scheduler_decision
  };
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
  const historian = mc("resolveHistorianFromConfig")(config);
  const model = String(historian?.model || "");
  return {
    enabled: Boolean(config.enabled),
    compaction_enabled: Boolean(config.compaction?.enabled),
    execute_threshold_percentage: config.execute_threshold_percentage,
    execute_threshold_tokens: config.execute_threshold_tokens ?? null,
    cache_ttl: config.cache_ttl,
    history_budget_percentage: Number(config.history_budget_percentage || 0.15),
    protected_tags: Number(config.protected_tags || 20),
    commit_cluster_trigger: config.commit_cluster_trigger ?? null,
    historian_timeout_ms: Number(historian?.timeoutMs || config.historian_timeout_ms || 120000),
    historian_model: model,
    historian_two_pass: Boolean(historian?.twoPass),
    memory_enabled: Boolean(config.memory?.enabled),
    memory_budget_tokens: Number(config.memory?.injection_budget_tokens || 4000),
    memory_auto_promote: Boolean(config.memory?.auto_promote),
    dreamer_enabled: curateTaskRunnable(config),
    dreamer_model: String(config.dreamer?.model || model)
  };
}

function dreamerToolSchemas() {
  const tools = new Map();
  const pi = {
    registerTool(tool) {
      tools.set(tool.name, tool);
    },
    registerCommand() {}
  };
  mc("registerMagicContextTools")(pi, {
    db,
    ensureProjectRegistered: async () => {},
    resolveProjectIdentity: () => "magic-hermes-dreamer-tool-template",
    memoryEnabled: true,
    embeddingEnabled: true,
    gitCommitsEnabled: true,
    memoryToolEnabled: true,
    allowDreamerActions: true,
    dreamerEnabled: true,
    todowriteEnabled: false,
    compactionOff: false
  });
  return ["ctx_memory", "ctx_search"]
    .map((name) => tools.get(name))
    .filter(Boolean)
    .map((tool) => ({
      name: tool.name,
      description: String(tool.description || ""),
      parameters: tool.parameters || { type: "object", properties: {} }
    }));
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
    dreamerEnabled: mc("isDreamerRunnable")(config),
    todowriteEnabled: false,
    compactionOff: !Boolean(config.compaction?.enabled)
  });

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
      pendingHistorian: null,
      tagger: mc("createTagger")(),
      scheduler: null,
      schedulerConfigKey: "",
      lastUsage: { percentage: 0, inputTokens: 0 },
      contextLimit: 0,
      provider: "",
      modelKey: "",
      promptSurfaceRuntime: mc("createPromptSurfaceRuntime")({
        harness: "hermes",
        directory: projectRoot,
        warn: () => {}
      }),
      configLoadedFrom: [...(loaded.loadedFromPaths || [])],
      configWarnings: [...(loaded.warnings || [])]
    };
    sessions.set(sessionId, session);
  } else {
    session.projectRoot = projectRoot;
    session.projectIdentity = projectIdentity;
    session.config = config;
    session.scheduler = null;
    session.schedulerConfigKey = "";
    session.promptSurfaceRuntime = mc("createPromptSurfaceRuntime")({
      harness: "hermes",
      directory: projectRoot,
      warn: () => {}
    });
    session.configLoadedFrom = [...(loaded.loadedFromPaths || [])];
    session.configWarnings = [...(loaded.warnings || [])];
  }

  mc("recordSessionProjectIdentity")(db, sessionId, projectIdentity);
  await mc("ensureProjectRegisteredFromPiDirectory")(projectRoot, db);
  await captureTools(session);
  syncSessionPolicy(session);

  return {
    package_version: packageJson.version,
    project_identity: projectIdentity,
    config: configSummary(config),
    config_loaded_from: [...(loaded.loadedFromPaths || [])],
    config_warnings: [...(loaded.warnings || [])],
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
    sessionManager: {
      getSessionId: () => session.id,
      // ctx_expand intentionally re-reads the host branch so it can recover
      // complete toolCall/toolResult parts instead of FTS-only text.  Present
      // the normalized Hermes entries in Pi's public SessionEntry shape.
      getBranch: () => session.entries.map((entry) => ({
        type: "message",
        id: entry.id,
        version: entry.version,
        ...(entry.timestamp === undefined ? {} : { timestamp: entry.timestamp }),
        message: entry.message
      }))
    }
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

function applyMagicContextGuidance(session, messages, isCacheBusting = false) {
  const output = messages.map((message) => structuredClone(message));
  const injectionConfig = session.config.system_prompt_injection;
  if (injectionConfig?.enabled === false) {
    return output;
  }

  let systemIndex = output.findIndex((message) => message?.role === "system");
  if (systemIndex < 0) {
    // Hermes supplies the real system prompt on host requests. Runtime-only
    // probes may omit it; do not synthesize one here because stable message
    // indices are based on the host request shape.
    return output;
  }
  const existingSystemPrompt = textContent(output[systemIndex]?.content);
  const skipSignatures = injectionConfig?.skip_signatures || [];
  if (
    skipSignatures.some(
      (signature) =>
        typeof signature === "string" &&
        signature.length > 0 &&
        existingSystemPrompt.includes(signature)
    )
  ) {
    return output;
  }

  const surface = session.promptSurfaceRuntime.resolveGuidance(
    session.config.prompt_surface,
    session.modelKey || undefined
  );
  const block = mc("buildMagicContextBlock")({
    db,
    cwd: session.projectRoot,
    sessionId: session.id,
    memoryEnabled: session.config.memory?.enabled !== false,
    includeGuidance: true,
    protectedTags: session.config.protected_tags,
    ctxReduceCallable: Boolean(session.config.compaction?.enabled),
    dreamerEnabled: mc("isDreamerRunnable")(session.config),
    temporalAwarenessEnabled: session.config.temporal_awareness === true,
    cavemanTextCompressionEnabled:
      session.config.caveman_text_compression?.enabled === true,
    language: session.config.language,
    promptSurfacePreset: surface.preset,
    primaryGuidanceOverride: surface.primaryOverride,
    userMemoriesEnabled: mc("userMemoryCollectionEnabled")(session.config.dreamer),
    isCacheBusting,
    existingSystemPrompt
  });
  if (!block) {
    return output;
  }

  const composed = mc("composeMagicContextSystemPrompt")(existingSystemPrompt, block);
  const processed = mc("processSystemPromptForCache")({
    db,
    sessionId: session.id,
    systemPrompt: composed,
    isCacheBusting,
    promptSurfacePreset: surface.preset
  });
  if (systemIndex >= 0) {
    output[systemIndex] = {
      ...output[systemIndex],
      content: processed.systemPrompt
    };
  } else {
    systemIndex = 0;
    output.unshift({ role: "system", content: processed.systemPrompt });
  }
  return output;
}

function requestSystemHash(messages) {
  const systemText = messages
    .filter((message) => ["system", "developer"].includes(String(message?.role || "")))
    .map((message) => textContent(message?.content))
    .join("\n\0\n");
  return systemText
    ? createHash("sha256").update(systemText).digest("hex")
    : "";
}

function piContentToHermes(content) {
  if (typeof content === "string") {
    return content;
  }
  if (!Array.isArray(content)) {
    return textContent(content);
  }
  const parts = [];
  for (const part of content) {
    if (!part || typeof part !== "object") {
      continue;
    }
    if (part.type === "text") {
      parts.push({ type: "text", text: String(part.text || "") });
    } else if (part.type === "image" && part.mimeType && part.data) {
      parts.push({
        type: "image_url",
        image_url: { url: `data:${part.mimeType};base64,${part.data}` }
      });
    }
  }
  if (parts.length === 1 && parts[0].type === "text") {
    return parts[0].text;
  }
  return parts;
}

function m0M1State(
  session,
  messages,
  historyBudgetTokens,
  { compactionOff = false } = {}
) {
  const meta = mc("getOrCreateSessionMeta")(db, session.id);
  let cacheExpired = false;
  try {
    const ttlMs = mc("parseCacheTtl")(meta.cacheTtl);
    cacheExpired = mc("isPiHardCacheExpired")(
      Number(meta.lastResponseTime || 0),
      ttlMs,
      Date.now()
    );
  } catch {
    cacheExpired = false;
  }
  return {
    sessionId: session.id,
    projectIdentity: session.projectIdentity,
    projectDirectory: session.projectRoot,
    memoryEnabled: session.config.memory?.enabled !== false,
    injectDocs: session.config.dreamer?.inject_docs !== false,
    injectionBudgetTokens: Number(
      session.config.memory?.injection_budget_tokens || 4000
    ),
    historyBudgetTokens: Math.max(1, Number(historyBudgetTokens || 16000)),
    muralEnabled: session.config.mural?.enabled === true,
    compactionOff,
    hardSignals: {
      systemHash: requestSystemHash(messages),
      modelKey: session.modelKey || "",
      cacheExpired,
      lastResponseTime: Number(meta.lastResponseTime || 0)
    }
  };
}

async function renderUpstreamPiContext(
  session,
  transformed,
  historyBudgetTokens,
  { compactionOff = false, applyNudges = true, applyAutoSearch = true } = {}
) {
  const piMessages = [];
  const entryIds = [];
  const entryIdByRef = new Map();
  const originalByRef = new Map();
  for (const assigned of transformed.ingested.assigned) {
    const original = transformed.messages[assigned.inputIndex];
    const piMessage = toPiMessage(original);
    if (!piMessage) {
      continue;
    }
    piMessages.push(piMessage);
    entryIds.push(assigned.id);
    entryIdByRef.set(piMessage, assigned.id);
    originalByRef.set(piMessage, original);
  }

  const injection = mc("injectM0M1Pi")(
    m0M1State(session, transformed.messages, historyBudgetTokens, { compactionOff }),
    db,
    piMessages,
    entryIds,
    transformed.mutated
  );

  // Upstream runs sticky note nudges after the core transform/injection and
  // before auto-search. Use object identity to keep real entry ids aligned even
  // after m[0]/m[1] synthetic messages are prepended.
  if (applyNudges) {
    mc("applyNoteNudges")({
      sessionId: session.id,
      db,
      messages: piMessages,
      projectIdentity: session.projectIdentity,
      entryIds,
      entryIdByRef,
      isCacheBusting: transformed.mutated || injection.m0Materialized === true,
      syntheticLeadingCount: Number(injection.syntheticLeadingCount || 0)
    });
  }

  const autoOptions = mc("resolveAutoSearchFromConfig")(session.config);
  if (applyAutoSearch && autoOptions.enabled) {
    await mc("runAutoSearchHintForPi")({
      sessionId: session.id,
      db,
      messages: piMessages,
      entryIds,
      entryIdByRef,
      options: {
        ...autoOptions,
        projectPath: session.projectIdentity
      },
      ensureProjectRegistered: async () =>
        mc("ensureProjectRegisteredFromPiDirectory")(session.projectRoot, db)
    });
  }

  const prefix = [];
  for (const message of transformed.messages) {
    if (!["system", "developer"].includes(String(message?.role || ""))) {
      break;
    }
    prefix.push(structuredClone(message));
  }
  const rendered = [...prefix];
  for (const piMessage of piMessages) {
    const original = originalByRef.get(piMessage);
    if (original) {
      rendered.push(fromPiMessage(original, piMessage));
      continue;
    }
    if (piMessage?.role === "user") {
      rendered.push({
        role: "user",
        content: piContentToHermes(piMessage.content)
      });
    }
  }
  return { messages: rendered, injection };
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

async function maintenanceRun(args) {
  const session = getSession(args);
  await mc("ensureProjectRegisteredFromPiDirectory")(session.projectRoot, db);

  const snapshot = mc("getProjectEmbeddingSnapshot")(session.projectIdentity);
  let memoryEmbeddings = 0;
  if (snapshot?.enabled) {
    memoryEmbeddings = Number(
      (await mc("embedUnembeddedMemoriesForProject")(
        db,
        session.projectIdentity
      )) || 0
    );
  }

  const stale = mc("sweepStaleEmbeddingIdentitiesForProject")(
    db,
    session.projectIdentity
  );

  let gitSweep = false;
  const gitConfig = session.config.memory?.git_commit_indexing;
  if (gitConfig?.enabled) {
    await mc("sweepGitCommits")({
      directory: session.projectRoot,
      projectIdentity: session.projectIdentity,
      db,
      gitCommitIndexing: gitConfig
    });
    gitSweep = true;
  }

  let smartNotes = null;
  if (mc("isDreamerRunnable")(session.config)) {
    const leaseKey = mc("leaseKeyFor")(
      "evaluate-smart-notes",
      session.projectIdentity
    );
    const holderId = randomUUID();
    if (mc("acquireLease")(db, holderId, leaseKey)) {
      try {
        smartNotes = await mc("runDueCompiledSmartNoteChecks")({
          db,
          projectIdentity: session.projectIdentity,
          projectRoot: session.projectRoot,
          retinaHandoff: session.config.smart_notes?.retina_handoff === true
        });
      } finally {
        mc("releaseLease")(db, holderId, leaseKey);
      }
    }
  }

  let embeddingDrain = false;
  let embeddingLevel = "disabled";
  if (session.config.memory?.enabled !== false) {
    const coverage = mc("getEmbeddingCoverageStatus")(
      db,
      session.projectIdentity,
      session.id
    );
    if (coverage?.enabled) {
      const result = await mc("runEmbedDrain")(
        db,
        session.projectIdentity,
        session.id
      );
      embeddingDrain = true;
      embeddingLevel = String(result?.level || "unknown");
    }
  }

  return {
    memory_embeddings: memoryEmbeddings,
    stale_embedding_rows_deleted:
      Number(stale?.memoryRowsDeleted || 0) +
      Number(stale?.commitRowsDeleted || 0) +
      Number(stale?.chunkRowsDeleted || 0),
    git_sweep: gitSweep,
    smart_notes: smartNotes,
    embedding_drain: embeddingDrain,
    embedding_level: embeddingLevel
  };
}

async function renderContext(args) {
  const session = getSession(args);
  const messages = Array.isArray(args.messages) ? args.messages : [];

  // Upstream compaction-off mode keeps the knowledge layer active while
  // relinquishing all MC context-window mutation. In particular: no tags,
  // reductions, temporal/context-management markers, auto-search augmentation,
  // historian work, or synthetic reclaim state. Raw indexing still proceeds.
  if (!session.config.compaction?.enabled) {
    const ingested = ingest(session, messages, true);
    const passthrough = {
      messages: applyMagicContextGuidance(session, messages, false),
      ingested,
      mutated: false
    };
    const rendered = await renderUpstreamPiContext(
      session,
      passthrough,
      Number(args.history_budget_tokens || 0),
      {
        compactionOff: true,
        applyNudges: false,
        applyAutoSearch: false
      }
    );
    return {
      messages: rendered.messages,
      history: "",
      last_compartment_end: mc("getLastCompartmentEndMessage")(db, session.id),
      scheduler_decision: "disabled",
      pending_ops: mc("getPendingOps")(db, session.id).length,
      transformed: rendered.injection.injected === true,
      raw_message_count: ingested.raw.length,
      m0_materialized: rendered.injection.m0Materialized === true,
      m0_reason: rendered.injection.m0Reason ?? null,
      synthetic_leading_count: Number(rendered.injection.syntheticLeadingCount || 0)
    };
  }

  // Assign stable IDs first, but do not index the live user message before
  // auto-search. Otherwise the current prompt can retrieve itself as a
  // "related" message. Prior transcript rows are indexed through the ordinal
  // immediately before the latest user message, then the full raw snapshot is
  // indexed after the request-only hint has been decided.
  const ingested = ingest(session, messages, false);
  let latestUserOrdinal = 0;
  for (const assigned of ingested.assigned) {
    if (assigned.source?.role !== "user") {
      continue;
    }
    const ordinal = ingested.inputOrdinals.get(assigned.inputIndex);
    if (ordinal !== undefined) {
      latestUserOrdinal = Math.max(latestUserOrdinal, ordinal);
    }
  }
  if (latestUserOrdinal > 1 && ingested.raw.length > 0) {
    mc("indexMessagesAfterOrdinal")(
      db,
      session.id,
      ingested.raw,
      0,
      latestUserOrdinal - 1
    );
  }

  const transformed = applyMagicContextTransform(session, messages, {
    ingested,
    inject_tags: true,
    apply_pending: true
  });
  transformed.messages = applyMagicContextGuidance(
    session,
    transformed.messages,
    transformed.mutated || transformed.scheduler_decision === "execute"
  );
  const rendered = await renderUpstreamPiContext(
    session,
    transformed,
    Number(args.history_budget_tokens || 0)
  );
  if (ingested.raw.length > 0) {
    mc("indexMessagesAfterOrdinal")(
      db,
      session.id,
      ingested.raw,
      0,
      ingested.raw[ingested.raw.length - 1].ordinal
    );
  }
  return {
    messages: rendered.messages,
    history: "",
    last_compartment_end: mc("getLastCompartmentEndMessage")(db, session.id),
    scheduler_decision: transformed.scheduler_decision,
    pending_ops: transformed.pending_ops,
    transformed: true,
    m0_materialized: rendered.injection.m0Materialized === true,
    m0_reason: rendered.injection.m0Reason ?? null,
    synthetic_leading_count: Number(rendered.injection.syntheticLeadingCount || 0)
  };
}

function historianPrepare(args) {
  const session = getSession(args);
  const messages = Array.isArray(args.messages) ? args.messages : [];
  const ingested = ingest(session, messages, true);
  const priorCompartments = mc("getCompartments")(db, session.id);
  const lastEnd = mc("getLastCompartmentEndMessage")(db, session.id);
  const boundary = args.boundary_snapshot && typeof args.boundary_snapshot === "object"
    ? args.boundary_snapshot
    : null;
  const offset = Math.max(
    1,
    lastEnd + 1,
    Number(boundary?.offset || 0)
  );
  const rawEnd = ingested.raw.length > 0
    ? ingested.raw[ingested.raw.length - 1].ordinal
    : session.baseOrdinal;
  const protectLast = Math.max(0, Number(args.protect_last_n ?? 6));
  const protectedTailStart = boundary && Number(boundary.eligibleEndOrdinal || 0) > 0
    ? Number(boundary.eligibleEndOrdinal)
    : Math.max(offset, rawEnd - protectLast + 1);
  if (protectedTailStart <= offset) {
    session.pendingHistorian = null;
    return { ready: false, reason: "protected-tail" };
  }

  const historian = mc("resolveHistorianFromConfig")(session.config);
  if (!historian) {
    return { ready: false, reason: "historian-disabled" };
  }
  const model = String(historian.model || "");
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

  const leaseHolder = String(
    args.holder_id || `magic-hermes:${process.pid}:${session.id}`
  );
  const lease = mc("acquireCompartmentLease")(db, session.id, leaseHolder);
  if (!lease) {
    session.pendingHistorian = null;
    return { ready: false, reason: "lease-held" };
  }
  mc("updateSessionMeta")(db, session.id, { compartmentInProgress: true });
  session.pendingHistorian = {
    chunk,
    priorCompartments,
    inputMessages: messages,
    inputOrdinals: ingested.inputOrdinals,
    historyBudgetTokens: Number(args.history_budget_tokens || 0),
    prompt,
    systemPrompt,
    editorSystemPrompt,
    validatedDraft: null,
    leaseHolder,
    boundary
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
    two_pass: Boolean(historian.twoPass),
    timeout_ms: Number(historian.timeoutMs || 120000),
    boundary_snapshot: boundary
  };
}

function historianRenew(args) {
  const session = getSession(args);
  const pending = session.pendingHistorian;
  if (!pending?.leaseHolder) {
    return { renewed: false, reason: "no-pending-historian" };
  }
  return {
    renewed: Boolean(
      mc("renewCompartmentLease")(db, session.id, pending.leaseHolder)
    )
  };
}

async function historianPublish(args) {
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
  const lastNewEnd = compartments.reduce(
    (maximum, compartment) => Math.max(maximum, Number(compartment.endMessage || 0)),
    0
  );
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
    if (lastNewEnd > 0) {
      mc("queueDropsForCompartmentalizedMessages")(db, session.id, lastNewEnd);
      mc("recordProtectedTailPublicationFloor")(db, session.id, lastNewEnd + 1);
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

  // Match the upstream post-publication side effects that feed note nudges and
  // downstream primer/embedding pipelines. These are MC-owned state changes,
  // not Hermes scheduling policy.
  mc("onNoteTrigger")(db, session.id, "historian_complete");

  let primerCandidatesStored = 0;
  if (validation.primerCandidates?.length && session.projectIdentity) {
    try {
      const candidate = validation.primerCandidates[0];
      const originIndex = Number(candidate.originCompartmentIndex || 0);
      const origin =
        originIndex >= 1 && originIndex <= compartments.length
          ? compartments[originIndex - 1]
          : null;
      const startCompartment = origin ?? compartments[0];
      const endCompartment = origin ?? compartments.at(-1);
      const sourceStartMessageId =
        startCompartment?.startMessageId ||
        `ordinal:${startCompartment?.startMessage ?? pending.chunk.startIndex}`;
      const sourceEndMessageId =
        endCompartment?.endMessageId ||
        `ordinal:${endCompartment?.endMessage ?? lastNewEnd}`;
      const sourceMessage = providerFor(session).readMessageById(sourceStartMessageId);
      const sourceMessageTime =
        mc("parseSourceMessageTime")(sourceMessage?.version) ?? Date.now();
      const stored = mc("insertPrimerCandidates")(db, [
        {
          projectPath: session.projectIdentity,
          harness: "hermes",
          sessionId: session.id,
          question: candidate.question,
          sourceCompartmentStart: startCompartment?.startMessage,
          sourceCompartmentEnd: endCompartment?.endMessage,
          sourceStartMessageId,
          sourceEndMessageId,
          sourceMessageTime
        }
      ]);
      primerCandidatesStored = Array.isArray(stored) ? stored.length : 0;
    } catch {
      // Upstream treats primer candidate capture as a non-blocking side channel.
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

  let embeddingsUpdated = false;
  try {
    await mc("ensureProjectRegisteredFromPiDirectory")(session.projectRoot, db);
    const snapshot = mc("getProjectEmbeddingSnapshot")(session.projectIdentity);
    if (snapshot?.enabled) {
      await mc("embedPromotedFacts")(db, session.id, session.projectIdentity, promoted);
      const chunksToEmbed = compartments
        .map((compartment, index) => ({
          id: persistedIds[index],
          startMessage: compartment.startMessage,
          endMessage: compartment.endMessage,
          sourceChunkText: pending.chunk.text
        }))
        .filter((chunk) => typeof chunk.id === "number");
      await mc("embedAndStoreCompartmentChunks")(
        db,
        session.id,
        session.projectIdentity,
        chunksToEmbed
      );
      embeddingsUpdated = true;
    }
  } catch {
    // Upstream embedding dispatch is non-blocking with respect to historian
    // publication. The compartment itself remains authoritative on failure.
  }

  mc("updateSessionMeta")(db, session.id, { compartmentInProgress: false });
  if (pending.leaseHolder) {
    mc("releaseCompartmentLease")(db, session.id, pending.leaseHolder);
  }
  const inputMessages = pending.inputMessages;
  const historyBudgetTokens = pending.historyBudgetTokens;
  session.pendingHistorian = null;

  // Materialize the post-publication request through the same authoritative
  // upstream renderer used by every normal Hermes turn. This applies queued
  // drops/cache policy and m[0]/m[1] consistently; do not keep a second
  // hand-written <session-history> rendering seam for synchronous /compress.
  const rendered = await renderContext({
    session_id: session.id,
    messages: inputMessages,
    history_budget_tokens: historyBudgetTokens
  });
  return {
    ok: true,
    compartments_added: compartments.length,
    memories_promoted: promoted.length,
    events_stored: eventsStored,
    user_memory_candidates_stored: userCandidatesStored,
    primer_candidates_stored: primerCandidatesStored,
    embeddings_updated: embeddingsUpdated,
    messages: rendered.messages,
    history: rendered.history,
    last_compartment_end: rendered.last_compartment_end
  };
}

function historianAbort(args) {
  const session = getSession(args);
  const pending = session.pendingHistorian;
  mc("updateSessionMeta")(db, session.id, { compartmentInProgress: false });
  if (pending?.leaseHolder) {
    mc("releaseCompartmentLease")(db, session.id, pending.leaseHolder);
  }
  session.pendingHistorian = null;
  return { aborted: true };
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

function parseHermesSourceTime(sourceVersion, updatedAt) {
  let value = sourceVersion;
  if (typeof value === "string") {
    const typed = value.match(/^(?:number|string):(.*)$/s);
    if (typed) {
      value = typed[1];
    }
  }
  return mc("parseSourceMessageTime")(value) ?? Number(updatedAt || 0);
}

function hermesIndexedRows(sessionId) {
  const rows = db.prepare(`
    SELECT s.session_id, s.message_id, s.message_ordinal, s.source_version,
           s.role, s.updated_at, f.content
      FROM message_history_source s
      JOIN message_history_fts f
        ON f.session_id = s.session_id AND f.message_id = s.message_id
     WHERE s.session_id = ? AND s.harness = 'hermes'
     ORDER BY s.message_ordinal ASC, s.message_id ASC
  `).all(sessionId);
  return rows.map((row) => ({
    sessionId: String(row.session_id),
    id: String(row.message_id),
    ordinal: Number(row.message_ordinal),
    role: String(row.role),
    text: String(row.content || ""),
    content: String(row.content || ""),
    ts: parseHermesSourceTime(row.source_version, row.updated_at)
  }));
}

function createHermesRetrospectiveProvider(projectIdentity) {
  return {
    async listProjectSessions() {
      return db.prepare(`
        SELECT session_id, updated_at
          FROM session_projects
         WHERE project_path = ? AND harness = 'hermes'
         ORDER BY updated_at ASC, session_id ASC
      `).all(projectIdentity).map((row) => ({
        sessionId: String(row.session_id),
        updatedAt: Number(row.updated_at || 0)
      }));
    },
    async readUserMessagesSince(sessionId, sinceMs, capPerSession) {
      const eligible = hermesIndexedRows(sessionId)
        .filter((row) => row.role === "user" && row.ts > sinceMs)
        .sort((a, b) => a.ts - b.ts || a.ordinal - b.ordinal);
      const limit = Math.max(1, Number(capPerSession || 1));
      return {
        messages: eligible.slice(0, limit),
        truncated: eligible.length > limit
      };
    },
    async readOldestMessageTimesSince(sessionIds, sinceMs) {
      const result = new Map();
      for (const sessionId of sessionIds) {
        const oldest = hermesIndexedRows(sessionId)
          .filter((row) => row.role === "user" && row.ts > sinceMs)
          .sort((a, b) => a.ts - b.ts || a.ordinal - b.ordinal)[0];
        if (oldest) {
          result.set(sessionId, oldest.ts);
        }
      }
      return result;
    },
    async readUserMessagesBefore(sessionId, beforeMs, count) {
      return hermesIndexedRows(sessionId)
        .filter((row) => row.role === "user" && row.ts < beforeMs)
        .sort((a, b) => b.ts - a.ts || b.ordinal - a.ordinal)
        .slice(0, Math.max(0, Number(count || 0)))
        .reverse();
    },
    dispose() {}
  };
}

async function createHermesPrimerRawProvider(sessionId) {
  const rows = hermesIndexedRows(sessionId);
  if (rows.length === 0) {
    return null;
  }
  const messages = rows.map((row) => ({
    id: row.id,
    ordinal: row.ordinal,
    role: row.role,
    // Primer seed/orientation code consumes the same raw-message contract as
    // Pi's convertEntriesToRawMessages(): iterable content parts plus a stable
    // source version. The durable Hermes message index intentionally stores
    // searchable canonical text, so replay it as a text part here.
    parts: [{ type: "text", text: row.text }],
    version: row.ts
  }));
  return {
    readMessages() {
      return messages;
    },
    getMessageCount() {
      return messages.length;
    }
  };
}

const dreamerVirtualSessions = new Map();

function dreamerModelRef(model) {
  if (!model || typeof model !== "object") {
    return "";
  }
  const provider = String(model.providerID || "").trim();
  const name = String(model.modelID || "").trim();
  return provider && name ? `${provider}/${name}` : name;
}

function createHermesDreamerClient(parentSession) {
  return {
    session: {
      async list() {
        return [{ id: parentSession.id, cwd: parentSession.projectRoot }];
      },
      async get({ path }) {
        if (path?.id === parentSession.id) {
          return { id: parentSession.id, cwd: parentSession.projectRoot };
        }
        return dreamerVirtualSessions.get(String(path?.id || "")) ?? null;
      },
      async create({ body, query }) {
        const id = `mh-dream-${randomUUID()}`;
        const record = {
          id,
          parentID: body?.parentID ?? parentSession.id,
          title: String(body?.title || "magic-context-dreamer"),
          directory: String(query?.directory || parentSession.projectRoot),
          messages: [],
          childHandle: null
        };
        dreamerVirtualSessions.set(id, record);
        return { id };
      },
      async prompt({ path, body, query, signal }) {
        const id = String(path?.id || "");
        const record = dreamerVirtualSessions.get(id);
        if (!record) {
          throw new Error(`Unknown Hermes Dreamer virtual session: ${id}`);
        }
        const prompt = (body?.parts || [])
          .filter((part) => part?.type === "text")
          .map((part) => String(part.text || ""))
          .join("\n");
        const callbackPromise = hostCallback("dreamer_child_prompt", {
          virtual_session_id: id,
          parent_session_id: parentSession.id,
          title: record.title,
          directory: String(query?.directory || record.directory),
          agent: String(body?.agent || "dreamer"),
          system: String(body?.system || ""),
          prompt,
          model: dreamerModelRef(body?.model)
        });
        let result;
        if (signal) {
          result = await Promise.race([
            callbackPromise,
            new Promise((_, reject) => {
              if (signal.aborted) {
                reject(new Error("Dreamer child prompt aborted"));
                return;
              }
              signal.addEventListener(
                "abort",
                () => reject(new Error("Dreamer child prompt aborted")),
                { once: true }
              );
            })
          ]);
        } else {
          result = await callbackPromise;
        }
        const text = String(result?.text || result?.summary || "");
        if (!text.trim()) {
          throw new Error("Hermes Dreamer child returned no assistant output");
        }
        record.childHandle = result?.handle ?? null;
        const toolParts = Array.isArray(result?.tool_history)
          ? result.tool_history.map((item) => ({
              type: "tool",
              tool: String(item?.tool_name || "unknown"),
              state: {
                input:
                  item?.tool_input && typeof item.tool_input === "object"
                    ? item.tool_input
                    : {},
                metadata: {
                  input_bytes: Number(item?.input_bytes || 0),
                  output_bytes: Number(item?.output_bytes || 0),
                  status: String(item?.status || "")
                }
              }
            }))
          : [];
        record.messages.push({
          info: { role: "assistant", time: { created: Date.now() } },
          parts: [...toolParts, { type: "text", text }]
        });
        return { ok: true };
      },
      async messages({ path, query }) {
        const record = dreamerVirtualSessions.get(String(path?.id || ""));
        if (!record) {
          return [];
        }
        const limit = Math.max(1, Number(query?.limit || record.messages.length || 1));
        return record.messages.slice(-limit);
      },
      async abort({ path }) {
        const id = String(path?.id || "");
        const record = dreamerVirtualSessions.get(id);
        if (record) {
          await hostCallback("dreamer_child_abort", {
            handle: record.childHandle,
            virtual_session_id: id,
            reason: "Magic Context Dreamer aborted the child run"
          });
        }
        return { ok: true };
      },
      async delete({ path }) {
        dreamerVirtualSessions.delete(String(path?.id || ""));
        return { ok: true };
      }
    }
  };
}

async function dreamerExecution(session) {
  const dreamer = session.config.dreamer;
  if (!dreamer || dreamer.disable === true || !mc("isDreamerRunnable")(session.config)) {
    return null;
  }
  await mc("ensureProjectRegisteredFromPiDirectory")(session.projectRoot, db);
  const tasks = mc("buildDreamTaskRuntimeConfigs")(
    dreamer,
    session.config.language
  );
  const client = createHermesDreamerClient(session);
  const executor = mc("createDreamTaskExecutor")({
    client,
    sessionDirectory: session.projectRoot,
    openOpenCodeDb: () => null,
    retrospectiveRawProvider: () =>
      createHermesRetrospectiveProvider(session.projectIdentity),
    primerRawProviderFactory: createHermesPrimerRawProvider,
    userMemoryCollectionEnabled: mc("userMemoryCollectionEnabled")(dreamer),
    ensureProjectRegistered: (directory, database) =>
      mc("ensureProjectRegisteredFromPiDirectory")(directory, database || db),
    language: session.config.language,
    dreamerModel: dreamer.model,
    mural: session.config.mural,
    memoryInjectionBudgetTokens: Number(
      session.config.memory?.injection_budget_tokens || 4000
    ),
    retinaHandoff: session.config.smart_notes?.retina_handoff === true,
    transformMode: session.config.transform_mode || "ts",
    moduleClient: undefined,
    onProgress: () => {}
  });
  return { dreamer, tasks, executor };
}

async function dreamerRunDue(args) {
  const session = getSession(args);
  const execution = await dreamerExecution(session);
  if (!execution) {
    return { ran: 0, reason: "dreamer-disabled" };
  }
  const ran = await mc("runDueTasksForProject")({
    db,
    projectIdentity: session.projectIdentity,
    tasks: execution.tasks,
    executor: execution.executor
  });
  return {
    ran: Number(ran || 0),
    task_count: execution.tasks.length,
    project_identity: session.projectIdentity
  };
}

async function dreamerRunManual(args) {
  const session = getSession(args);
  const execution = await dreamerExecution(session);
  if (!execution) {
    return { ran: [], failed: [], reason: "dreamer-disabled" };
  }
  const task = String(args.task || "").trim();
  const result = await mc("runManualDream")({
    db,
    projectIdentity: session.projectIdentity,
    tasks: execution.tasks,
    executor: execution.executor,
    ...(task ? { task } : {})
  });
  return result;
}

function runtimeDoctor(args = {}) {
  const sessionId = String(args.session_id || "");
  const session = sessionId ? sessions.get(sessionId) : null;
  let databaseHealth = "unknown";
  try {
    const rows = db.prepare("PRAGMA quick_check").all();
    const first = rows?.[0];
    databaseHealth = String(
      first?.quick_check ?? first?.integrity_check ?? Object.values(first || {})[0] ?? "unknown"
    );
  } catch (error) {
    databaseHealth = "error:" + (error instanceof Error ? error.message : String(error));
  }
  return {
    package_version: packageJson.version,
    package_root: packageRoot,
    harness: "hermes",
    supported_series: SUPPORTED_SERIES.join("."),
    core_symbols_ready: missingCoreSymbols.length === 0,
    missing_core_symbols: [...missingCoreSymbols],
    database_health: databaseHealth,
    session_bound: Boolean(session),
    project_identity: session?.projectIdentity ?? null,
    project_root: session?.projectRoot ?? null,
    compaction_enabled: session ? Boolean(session.config.compaction?.enabled) : null,
    config_loaded_from: session ? [...(session.configLoadedFrom || [])] : [],
    config_warnings: session ? [...(session.configWarnings || [])] : []
  };
}

let nextHostCallbackId = 1;
const pendingHostCallbacks = new Map();

function hostCallback(method, params = {}) {
  const callbackId = nextHostCallbackId++;
  return new Promise((resolvePromise, rejectPromise) => {
    pendingHostCallbacks.set(callbackId, {
      resolve: resolvePromise,
      reject: rejectPromise
    });
    process.stdout.write(JSON.stringify({
      type: "host_callback",
      callback_id: callbackId,
      method,
      params
    }) + "\n");
  });
}

function resolveHostCallback(message) {
  const callbackId = Number(message.callback_id);
  const pending = pendingHostCallbacks.get(callbackId);
  if (!pending) {
    return;
  }
  pendingHostCallbacks.delete(callbackId);
  if (message.error) {
    const text =
      typeof message.error === "object"
        ? String(message.error.message || "host callback failed")
        : String(message.error);
    pending.reject(new Error(text));
  } else {
    pending.resolve(message.result);
  }
}

const methods = {
  hello: async () => ({
    package_root: packageRoot,
    package_version: packageJson.version,
    harness: "hermes"
  }),
  doctor: async (args) => runtimeDoctor(args),
  dreamer_tool_schemas: async () => dreamerToolSchemas(),
  bind,
  model_update: async (args) => updateModel(args),
  usage_update: async (args) => updateUsage(args),
  observe: async (args) => observe(args),
  maintenance_run: async (args) => maintenanceRun(args),
  render_context: async (args) => renderContext(args),
  tool: executeTool,
  pressure_state: async (args) => pressureState(args),
  historian_decide: async (args) => historianDecision(args),
  historian_prepare: async (args) => historianPrepare(args),
  historian_renew: async (args) => historianRenew(args),
  historian_publish: async (args) => historianPublish(args),
  historian_abort: async (args) => historianAbort(args),
  memory_context: async (args) => memoryContext(args),
  dreamer_run_due: async (args) => dreamerRunDue(args),
  dreamer_run_manual: async (args) => dreamerRunManual(args),
  close: async () => {
    mc("closeQuietly")(db);
    return { closed: true };
  }
};

const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
let requestChain = Promise.resolve();

async function handleTopLevelRequest(request) {
  try {
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

lines.on("line", (line) => {
  if (!line.trim()) {
    return;
  }
  let message;
  try {
    message = JSON.parse(line);
  } catch (error) {
    process.stdout.write(JSON.stringify({
      id: null,
      error: { message: "Invalid runtime request JSON" }
    }) + "\n");
    return;
  }
  if (message?.type === "host_callback_result") {
    resolveHostCallback(message);
    return;
  }
  requestChain = requestChain.then(() => handleTopLevelRequest(message));
});

await new Promise((resolveClose) => lines.on("close", resolveClose));
await requestChain;
for (const pending of pendingHostCallbacks.values()) {
  pending.reject(new Error("Runtime input closed during host callback"));
}
pendingHostCallbacks.clear();
