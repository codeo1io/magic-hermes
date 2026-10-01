// One-shot migration driver for `magic-hermes db migrate`.
//
// Imports the upstream storage core directly (bypassing the bridge's adapter
// loader) and calls its sanctioned `openDatabase()` entry point, which runs
// the schema fence, the migration-on-open holder guard, and `runMigrations()`
// — exactly what a Pi/OMP/hermes harness boot would run, with no other work.
//
// Contract (stdout is always a single JSON object):
//   exit 0  action=opened|none  — store is open and at the package's fence
//   exit 3  action=refused      — migration-on-open guard refused (blockers)
//   exit 1  action=error        — package/chunk unusable or unexpected failure
//
// The Python side (cli db-migrate loop) interprets this and drives the
// drain/retry cadence; this file deliberately contains no waiting, no
// retries, and no process management.
import { register } from "node:module";
import { pathToFileURL } from "node:url";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

function parseArgs(argv) {
  const args = { db: null, packageRoot: null, json: false };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--db" && argv[i + 1]) {
      args.db = argv[++i];
    } else if (argv[i] === "--package-root" && argv[i + 1]) {
      args.packageRoot = argv[++i];
    } else if (argv[i] === "--json") {
      args.json = true;
    }
  }
  return args;
}

function findStorageChunk(packageRoot) {
  const dist = join(packageRoot, "dist");
  for (const name of readdirSync(dist)) {
    if (!name.startsWith("index-") || !name.endsWith(".js")) {
      continue;
    }
    const candidate = join(dist, name);
    let text;
    try {
      text = readFileSync(candidate, "utf8");
    } catch {
      continue;
    }
    if (
      text.includes("function openDatabase(") &&
      text.includes("function getPersistedSchemaVersion(")
    ) {
      return candidate;
    }
  }
  return null;
}

function emit(payload) {
  process.stdout.write(JSON.stringify(payload) + "\n");
}

const here = dirname(fileURLToPath(import.meta.url));
const args = parseArgs(process.argv.slice(2));
if (!args.db) {
  emit({ action: "error", error: "--db <path> is required" });
  process.exit(1);
}

register(pathToFileURL(join(here, "migrate-hooks.mjs")));

let packageRoot = args.packageRoot;
if (!packageRoot) {
  packageRoot = process.env.MAGIC_CONTEXT_PACKAGE_ROOT || null;
}
if (!packageRoot) {
  emit({
    action: "error",
    error: "no package root: pass --package-root or set MAGIC_CONTEXT_PACKAGE_ROOT",
  });
  process.exit(1);
}

let chunk;
try {
  chunk = findStorageChunk(packageRoot);
} catch (error) {
  emit({
    action: "error",
    error: `package root unreadable at ${packageRoot}: ${error?.message ?? error}`,
  });
  process.exit(1);
}
if (chunk === null) {
  emit({
    action: "error",
    error: `storage core chunk not found under ${packageRoot}/dist ` +
      "(no index-*.js exports openDatabase) — package layout changed upstream?",
  });
  process.exit(1);
}

let mod;
try {
  mod = await import(pathToFileURL(chunk).href);
} catch (error) {
  emit({
    action: "error",
    error: `storage core import failed: ${error?.message ?? error}`,
  });
  process.exit(1);
}

const latest = mod.LATEST_SUPPORTED_VERSION;
if (typeof latest !== "number") {
  emit({
    action: "error",
    error: "LATEST_SUPPORTED_VERSION missing from the storage core — " +
      "incompatible upstream package",
  });
  process.exit(1);
}

let db = null;
try {
  db = mod.openDatabase({ dbPath: args.db });
} catch (error) {
  emit({
    action: "error",
    error: `openDatabase threw: ${error?.message ?? error}`,
  });
  process.exit(1);
}

if (db === null) {
  const refusal = mod.getMigrationOnOpenRefusal?.() ?? null;
  const fence = mod.getSchemaFenceRejection?.() ?? null;
  emit({
    action: "refused",
    latest_supported_version: latest,
    persisted_version: refusal?.persistedVersion ?? fence?.persistedVersion ?? null,
    supported_version: refusal?.supportedVersion ?? fence?.supportedVersion ?? null,
    blockers: refusal?.serverPids ?? [],
    blocking_processes:
      refusal?.blockingProcesses?.map((entry) => ({
        kind: entry?.kind ?? "unknown",
        pid: entry?.pid ?? null,
      })) ?? [],
    fence: fence
      ? {
          persisted_version: fence.persistedVersion,
          supported_version: fence.supportedVersion,
        }
      : null,
  });
  process.exit(3);
}

let persisted = null;
try {
  persisted = mod.getPersistedSchemaVersion(db);
} catch {}
try {
  mod.closeDatabase(db);
} catch {}

emit({
  action: persisted === latest ? "none" : "opened",
  latest_supported_version: latest,
  persisted_version: persisted,
});
process.exit(0);
