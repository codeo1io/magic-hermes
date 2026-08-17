#!/usr/bin/env node
// magic-hermes bridge daemon.
//
// A zero-dependency Node daemon that speaks the subc wire protocol (envelope
// v2, HMAC-SHA256 handshake) and serves the mc-module method surface that the
// magic-hermes Python connector (src/magic_hermes/) calls. Storage is backed
// by the shared Magic Context SQLite database plus a bridge-local hermes.db
// for session bookkeeping.
//
// Run: node bridge/daemon.mjs [--db PATH] [--runtime-dir DIR] [--project-root DIR]
// Connection file (schema 1, wire_version 2) is written 0600 into the runtime
// dir as `subc-hermes.json`.

import net from "node:net";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";

const PROTOCOL_VERSION = 2;
const HEADER_LEN = 21;
const MAX_FRAME_BODY_LEN = 64 * 1024 * 1024;
const FRAME_TYPE = { RESPONSE: 1, ERROR: 5, PONG: 8 };
const DAEMON_VER = "magic-hermes-bridge/0.1.0";
const MODULE_ID = "mc-bridge-hermes";

// ---------------------------------------------------------------- CLI args

function arg(name, fallback) {
	const i = process.argv.indexOf(`--${name}`);
	if (i === -1 || i + 1 >= process.argv.length) return fallback;
	return process.argv[i + 1];
}

const defaultDataDir =
	process.env.XDG_DATA_HOME ||
	path.join(process.env.home || "/home/agent", ".local/share");
const dbPath = arg(
	"db",
	path.join(defaultDataDir, "cortexkit/magic-context/context.db"),
);
const runtimeDir = arg(
	"runtime-dir",
	process.env.XDG_RUNTIME_HOME ||
		path.join(defaultDataDir, "cortexkit/magic-context/rpc"),
);
const projectRoot = arg("project-root", process.cwd());
const host = arg("host", "127.0.0.1");

// ---------------------------------------------------------------- sqlite

const shared = new DatabaseSync(dbPath);
shared.exec("PRAGMA journal_mode = WAL; PRAGMA busy_timeout = 5000;");
// Ensure an FTS5 index over memories exists and stays in sync (external
// content table + triggers, created idempotently; safe on an existing DB).
try {
	shared.exec(`
  CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content, content='memories', content_rowid='id', tokenize='porter unicode61');
  CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content); END;
  CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content)
    VALUES ('delete', new.id, new.content); END;
  CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content)
    VALUES ('delete', old.id, old.content);
    INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content); END;
`);
	shared.exec(
		"INSERT INTO memories_fts(rowid, content) SELECT id, content FROM memories " +
			"WHERE id NOT IN (SELECT rowid FROM memories_fts)",
	);
} catch {
	/* FTS5 unavailable — LIKE fallback still serves queries */
}
const hermesDb = new DatabaseSync(path.join(runtimeDir, "hermes.db"));
hermesDb.exec(`
  CREATE TABLE IF NOT EXISTS mh_sessions (
    session_id TEXT PRIMARY KEY, platform TEXT, model TEXT,
    begun_at INTEGER, ended_at INTEGER, message_count INTEGER DEFAULT 0);
  CREATE TABLE IF NOT EXISTS mh_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL,
    prompt_tokens INTEGER, completion_tokens INTEGER, total_tokens INTEGER);
  CREATE TABLE IF NOT EXISTS mh_compartments (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
    seq INTEGER NOT NULL, start_message INTEGER NOT NULL, end_message INTEGER NOT NULL,
    title TEXT NOT NULL, created_at INTEGER NOT NULL);
`);

function projectKey() {
	try {
		const gitDir = path.join(projectRoot, ".git");
		if (fs.existsSync(gitDir)) {
			// Match the core's git:<sha> keying as closely as we can: hash of the
			// resolved repo (HEAD sha when readable, else the git dir itself).
			const head = fs.readFileSync(path.join(gitDir, "HEAD"), "utf8").trim();
			if (head.startsWith("ref: ")) {
				const ref = path.join(gitDir, head.slice(5));
				if (fs.existsSync(ref))
					return `git:${fs.readFileSync(ref, "utf8").trim()}`;
			}
			return `git:${crypto.createHash("sha256").update(gitDir).digest("hex")}`;
		}
	} catch {
		/* fall through to dir: key */
	}
	return `dir:${crypto.createHash("sha256").update(path.resolve(projectRoot)).digest("hex")}`;
}
const PROJECT_PATH = projectKey();

function now() {
	return Date.now();
}

// ---------------------------------------------------------------- handlers

function err(name, message) {
	const e = new Error(message);
	e.name = name;
	return e;
}

const handlers = {
	"session.begin": (p) => {
		hermesDb
			.prepare(
				"INSERT INTO mh_sessions (session_id, platform, model, begun_at) VALUES (?,?,?,?) " +
					"ON CONFLICT(session_id) DO UPDATE SET platform=excluded.platform, model=excluded.model",
			)
			.run(
				...[p.session_id ?? "", p.platform ?? "hermes", p.model ?? "", now()],
			);
		return { ok: true };
	},
	"session.end": (p) => {
		hermesDb
			.prepare(
				"UPDATE mh_sessions SET ended_at=?, message_count=? WHERE session_id=?",
			)
			.run(...[now(), p.message_count ?? 0, p.session_id ?? ""]);
		return { ok: true };
	},
	"session.observe_turn": (p) => {
		// Transcript retention: the connector's historian (U6) consumes turns
		// through hermes; the bridge only counts for bookkeeping.
		const sid = p.session_id ?? "";
		if (sid)
			hermesDb
				.prepare(
					"UPDATE mh_sessions SET message_count = message_count + ? WHERE session_id=?",
				)
				.run(...[Array.isArray(p.messages) ? p.messages.length : 0, sid]);
		return { ok: true };
	},
	"usage.report": (p) => {
		hermesDb
			.prepare(
				"INSERT INTO mh_usage (ts, prompt_tokens, completion_tokens, total_tokens) VALUES (?,?,?,?)",
			)
			.run(
				...[
					now(),
					p.prompt_tokens ?? 0,
					p.completion_tokens ?? 0,
					p.total_tokens ?? 0,
				],
			);
		return { ok: true };
	},

	"context.compact": (p) => {
		const messages = Array.isArray(p.messages) ? p.messages : [];
		if (messages.length === 0) return { messages: [] };
		const sessionId = p.session_id ?? "";
		// Retain leading system messages + a trailing window (half the transcript),
		// and record the compacted prefix as a compartment.
		const keepFrom = Math.max(
			messages.findIndex((m) => m.role !== "system") < 0
				? messages.length
				: messages.findIndex((m) => m.role !== "system"),
			Math.floor(messages.length / 2),
		);
		const head = messages.filter((m) => m.role === "system");
		const tail = messages.slice(keepFrom);
		const seq =
			hermesDb
				.prepare("SELECT COUNT(*) c FROM mh_compartments WHERE session_id=?")
				.get(sessionId ?? "").c + 1;
		hermesDb
			.prepare(
				"INSERT INTO mh_compartments (session_id, seq, start_message, end_message, title, created_at) VALUES (?,?,?,?,?,?)",
			)
			.run(
				...[
					sessionId,
					seq,
					0,
					keepFrom - 1,
					`compartment ${seq}: ${keepFrom} messages`,
					now(),
				],
			);
		const summary = {
			role: "assistant",
			content:
				`[dropped §${seq}§] Earlier conversation (${keepFrom} messages, compartments recorded) ` +
				`compacted by magic-hermes bridge; use ctx_search to recall details.`,
		};
		return { messages: [...head, summary, ...tail], compartment: seq };
	},
	"context.prune_tool_results": (p) => {
		const messages = Array.isArray(p.messages) ? p.messages : [];
		let pruned = 0;
		const out = messages.map((m) => {
			if (
				m.role === "tool" &&
				typeof m.content === "string" &&
				m.content.length > 4000
			) {
				pruned += 1;
				return {
					...m,
					content:
						m.content.slice(0, 1000) + "\n[pruned by magic-hermes bridge]",
				};
			}
			return m;
		});
		return { messages: out, pruned };
	},

	"memory.search": (p) => {
		const q = String(p.query ?? "").trim();
		const limit = Math.min(Number(p.limit ?? 10) || 10, 100);
		let rows = [];
		if (q) {
			try {
				rows = shared
					.prepare(
						"SELECT m.id, m.category, m.content, m.project_path FROM memories_fts f " +
							"JOIN memories m ON m.rowid = f.rowid WHERE memories_fts MATCH ? AND m.status='active' " +
							"ORDER BY rank LIMIT ?",
					)
					.all(ftsQuery(q), limit);
			} catch {
				rows = [];
			}
			if (rows.length === 0) {
				const terms = q.toLowerCase().split(/\s+/).filter(Boolean);
				const where = terms.map(() => "lower(content) LIKE ?").join(" AND ");
				rows = shared
					.prepare(
						"SELECT id, category, content, project_path FROM memories WHERE status='active' AND " +
							(where || " 1=1") +
							" ORDER BY updated_at DESC LIMIT ?",
					)
					.all(...[...terms.map((t) => `%${t}%`), limit]);
			}
		} else {
			rows = shared
				.prepare(
					"SELECT id, category, content, project_path FROM memories WHERE status='active' ORDER BY updated_at DESC LIMIT ?",
				)
				.all(limit);
		}
		return {
			results: rows.map((r) => ({
				id: r.id,
				category: r.category,
				content: r.content,
				score: 1.0,
			})),
		};
	},
	"memory.write": (p) => {
		const content = String(p.content ?? "").trim();
		if (!content) throw err("invalid_params", "content required");
		const category = String(p.category ?? "PROJECT_RULES").toUpperCase();
		const hash = crypto
			.createHash("sha256")
			.update(content.toLowerCase().replace(/\s+/g, " "))
			.digest("hex");
		const t = now();
		const dup = shared
			.prepare(
				"SELECT id FROM memories WHERE normalized_hash=? AND status='active'",
			)
			.get(hash);
		if (dup) {
			shared
				.prepare(
					"UPDATE memories SET seen_count=seen_count+1, last_seen_at=? WHERE id=?",
				)
				.run(...[t, dup.id]);
			return { id: dup.id, duplicate: true };
		}
		shared
			.prepare(
				"INSERT INTO memories (project_path, category, content, normalized_hash, scope, source_type, " +
					"source_session_id, first_seen_at, created_at, updated_at, last_seen_at, status) " +
					"VALUES (?,?,?,?,'project','hermes-bridge',?,?,?,?,?,'active')",
			)
			.run(
				...[
					PROJECT_PATH,
					category,
					content,
					hash,
					p.session_id ?? "",
					t,
					t,
					t,
					t,
				],
			);
		return {
			id: Number(shared.prepare("SELECT last_insert_rowid() id").get().id),
			duplicate: false,
		};
	},
	"memory.list": (_p) => {
		const rows = shared
			.prepare(
				"SELECT id, category, content FROM memories WHERE status='active' ORDER BY id DESC LIMIT 500",
			)
			.all();
		return { memories: rows };
	},
	"memory.archive": (p) => {
		const ids = Array.isArray(p.ids) ? p.ids : [];
		for (const id of ids) {
			shared
				.prepare(
					"UPDATE memories SET status='archived', updated_at=? WHERE id=?",
				)
				.run(...[now(), id]);
		}
		return { ok: true, archived: ids.length };
	},
	"memory.expand": (p) => {
		const row = shared
			.prepare("SELECT id, category, content, status FROM memories WHERE id=?")
			.get(p.id ?? -1);
		if (!row) throw err("not_found", `memory ${p.id} not found`);
		return row;
	},
	"memory.reduce": (_p) => ({ ok: true, dropped: [] }),
	"memory.manage": (p) => {
		const action = p.action;
		if (action === "write") return handlers["memory.write"](p);
		if (action === "search") return handlers["memory.search"](p);
		if (action === "list") return handlers["memory.list"](p);
		if (action === "archive") return handlers["memory.archive"](p);
		if (action === "expand") return handlers["memory.expand"](p);
		throw err("invalid_params", `unknown memory action ${action}`);
	},

	"notes.manage": (p) => {
		const action = p.action;
		if (action === "write") {
			const t = now();
			shared
				.prepare(
					"INSERT INTO notes (type, status, content, session_id, project_path, created_at, updated_at, harness) " +
						"VALUES ('session','active',?,?,?,?,?, 'hermes')",
				)
				.run(
					...[String(p.content ?? ""), p.session_id ?? "", PROJECT_PATH, t, t],
				);
			return { ok: true };
		}
		if (action === "read") {
			const rows = shared
				.prepare(
					"SELECT id, content, status, created_at FROM notes WHERE harness='hermes' ORDER BY id DESC LIMIT 100",
				)
				.all();
			return { notes: rows };
		}
		if (action === "update") {
			shared
				.prepare(
					"UPDATE notes SET content=?, updated_at=? WHERE id=? AND harness='hermes'",
				)
				.run(...[String(p.content ?? ""), now(), p.note_id ?? -1]);
			return { ok: true };
		}
		if (action === "dismiss") {
			shared
				.prepare(
					"UPDATE notes SET status='dismissed', updated_at=? WHERE id=? AND harness='hermes'",
				)
				.run(...[now(), p.note_id ?? -1]);
			return { ok: true };
		}
		throw err("invalid_params", `unknown note action ${action}`);
	},
	"notes.status": (_p) => {
		const rows = shared
			.prepare(
				"SELECT id, content FROM notes WHERE harness='hermes' AND status='active' ORDER BY id DESC LIMIT 20",
			)
			.all();
		return { notes: rows };
	},
};

function ftsQuery(q) {
	// Quote each term for FTS5 safety.
	return q
		.split(/\s+/)
		.filter(Boolean)
		.map((t) => `"${t.replace(/"/g, '""')}"`)
		.join(" ");
}

// ---------------------------------------------------------------- envelope

function encodeHeader(h) {
	const b = Buffer.alloc(HEADER_LEN);
	b.writeUInt32LE(h.len, 0);
	b[4] = h.ver;
	b[5] = h.ty;
	b[6] = h.flags;
	b.writeUInt16LE(h.channel, 7);
	b.writeUInt32LE(h.epoch, 9);
	b.writeBigUint64LE(h.corr, 13);
	return b;
}

function decodeHeader(buf) {
	if (buf.length < HEADER_LEN) return null;
	const ver = buf[4];
	if (ver !== PROTOCOL_VERSION)
		throw err("unsupported_version", `version ${ver}`);
	const ty = buf[5];
	if (ty > 11) throw err("unknown_frame_type", `type byte ${ty}`);
	return {
		len: buf.readUInt32LE(0),
		ver,
		ty,
		flags: buf[6],
		channel: buf.readUInt16LE(7),
		epoch: buf.readUInt32LE(9),
		corr: buf.readBigUInt64LE(13),
	};
}

function frame(ty, channel, epoch, corr, bodyObj) {
	const body =
		bodyObj === undefined
			? Buffer.alloc(0)
			: Buffer.from(JSON.stringify(bodyObj), "utf8");
	return Buffer.concat([
		encodeHeader({
			len: body.length,
			ver: PROTOCOL_VERSION,
			ty,
			flags: 0,
			channel,
			epoch,
			corr,
		}),
		body,
	]);
}

// ---------------------------------------------------------------- auth

function hmacRaw(key, domain, ...parts) {
	const h = crypto.createHmac("sha256", key);
	h.update(domain, "utf8");
	for (const p of parts) h.update(p);
	return h.digest();
}

function hmac(key, ...parts) {
	const h = crypto.createHmac("sha256", key);
	h.update(parts.join(":"));
	return h.digest("hex");
}

// ---------------------------------------------------------------- server

const KEY = crypto.randomBytes(32);
const DAEMON_ID = crypto.randomBytes(16);
const server = net.createServer({ host }, (sock) => handleConn(sock));

let nextChannel = 1;

function handleConn(sock) {
	sock.setNoDelay(true);
	const state = { authed: false, routes: new Map(), buf: Buffer.alloc(0) };

	function fail(message) {
		try {
			sock.write(
				frame(FRAME_TYPE.ERROR, 0, 0, 0n, {
					code: "handshake_failed",
					message,
				}),
			);
		} catch {}
		sock.destroy();
	}

	// Auth handshake: raw 4-byte LE length-prefixed JSON objects
	// (mirrors subc-transport auth.rs), not envelope frames.
	function writeAuth(obj) {
		const b = Buffer.from(JSON.stringify(obj), "utf8");
		const len = Buffer.alloc(4);
		len.writeUInt32LE(b.length, 0);
		sock.write(Buffer.concat([len, b]));
	}

	function readAuthMessage() {
		if (state.buf.length < 4) return null;
		const len = state.buf.readUInt32LE(0);
		if (state.buf.length < 4 + len) return null;
		const b = state.buf.subarray(4, 4 + len);
		state.buf = state.buf.subarray(4 + len);
		return JSON.parse(b.toString("utf8") || "{}");
	}

	const readFrames = () => {
		for (;;) {
			if (!state.authed) {
				const msg = readAuthMessage();
				if (!msg) return;
				if (msg.client_nonce && !state.clientNonce) {
					const cn = Buffer.from(msg.client_nonce);
					const sn = crypto.randomBytes(32);
					state.clientNonce = cn;
					state.serverNonce = sn;
					writeAuth({
						daemon_id: Array.from(DAEMON_ID),
						server_nonce: Array.from(sn),
						daemon_ver: DAEMON_VER,
						server_proof: Array.from(
							hmacRaw(KEY, "subc-server-v1", cn, sn, DAEMON_ID),
						),
					});
					return;
				}
				if (msg.client_auth) {
					const expected = hmacRaw(
						KEY,
						"subc-client-v1",
						state.clientNonce,
						state.serverNonce,
						DAEMON_ID,
					);
					if (!expected.equals(Buffer.from(msg.client_auth)))
						return fail("bad client proof");
					state.authed = true;
					return;
				}
				return fail("expected handshake messages");
			}
			if (state.buf.length < HEADER_LEN) return;
			let h;
			try {
				h = decodeHeader(state.buf.subarray(0, HEADER_LEN));
			} catch (e) {
				return fail(e.message);
			}
			if (state.buf.length < HEADER_LEN + h.len) return;
			const body = state.buf.subarray(HEADER_LEN, HEADER_LEN + h.len);
			state.buf = state.buf.subarray(HEADER_LEN + h.len);
			try {
				handleFrame(h, body);
			} catch (e) {
				const corr = h.corr ?? 0n;
				const channel = h.channel ?? 0;
				const epoch = h.epoch ?? 0;
				const code =
					e.name === "invalid_params" || e.name === "not_found"
						? e.name
						: "internal_error";
				sock.write(
					frame(FRAME_TYPE.ERROR, channel, epoch, corr, {
						code,
						message: e.message,
					}),
				);
			}
		}
	};

	function handleFrame(h, body) {
		if (h.channel === 0) {
			const op = JSON.parse(body.toString("utf8") || "{}");
			if (h.ty === 7) return sock.write(frame(FRAME_TYPE.PONG, 0, 0, h.corr));
			if (op.op === "catalog.list") {
				return sock.write(
					frame(FRAME_TYPE.RESPONSE, 0, 0, h.corr, {
						op: "catalog.list",
						modules: [
							{
								module_id: MODULE_ID,
								name: "magic-hermes bridge",
								version: "0.1.0",
							},
						],
					}),
				);
			}
			if (op.op === "route.open") {
				if ((op.target ?? {}).module_id !== MODULE_ID) {
					return sock.write(
						frame(FRAME_TYPE.ERROR, 0, 0, h.corr, {
							code: "unknown_module",
							message: `module ${(op.target ?? {}).module_id} not served`,
						}),
					);
				}
				const channel = nextChannel++ & 0xffff || 1;
				const epoch = 1;
				state.routes.set(channel, epoch);
				return sock.write(
					frame(FRAME_TYPE.RESPONSE, 0, 0, h.corr, {
						route_channel: channel,
						route_epoch: epoch,
					}),
				);
			}
			return sock.write(
				frame(FRAME_TYPE.ERROR, 0, 0, h.corr, {
					code: "unknown_op",
					message: op.op ?? "",
				}),
			);
		}

		const epoch = state.routes.get(h.channel);
		if (epoch === undefined) {
			return sock.write(
				frame(FRAME_TYPE.ERROR, h.channel, h.epoch, h.corr, {
					code: "unknown_channel",
					message: `channel ${h.channel} not routed`,
				}),
			);
		}
		if (h.ty === 7)
			return sock.write(frame(FRAME_TYPE.PONG, h.channel, epoch, h.corr));
		if (h.ty === 0 || h.ty === 2) {
			const req = JSON.parse(body.toString("utf8") || "{}");
			const handler = handlers[req.method];
			if (!handler) {
				return sock.write(
					frame(FRAME_TYPE.ERROR, h.channel, epoch, h.corr, {
						code: "method_not_found",
						message: req.method,
					}),
				);
			}
			const result = handler(req.params ?? {});
			return sock.write(
				frame(FRAME_TYPE.RESPONSE, h.channel, epoch, h.corr, {
					result: result ?? { ok: true },
				}),
			);
		}
		if (h.ty === 11 || h.ty === 6) {
			state.routes.delete(h.channel);
			return;
		}
	}

	sock.on("data", (d) => {
		state.buf = Buffer.concat([state.buf, d]);
		readFrames();
	});
	sock.on("error", () => sock.destroy());
}

// ---------------------------------------------------------------- boot

fs.mkdirSync(runtimeDir, { recursive: true });
server.listen(0, host, () => {
	const port = server.address().port;
	const connFile = {
		schema: 1,
		wire_version: PROTOCOL_VERSION,
		endpoints: [{ host, port }],
		key: Array.from(KEY),
		daemon_id: Array.from(DAEMON_ID),
		pid: process.pid,
		daemon_ver: DAEMON_VER,
	};
	const connPath = path.join(runtimeDir, "subc-hermes.json");
	fs.writeFileSync(connPath, JSON.stringify(connFile), { mode: 0o600 });
	try {
		fs.chmodSync(connPath, 0o600);
	} catch {}
	process.stdout.write(
		JSON.stringify({ conn_path: connPath, port, module_id: MODULE_ID }) + "\n",
	);
});
process.on("SIGTERM", () => process.exit(0));
process.on("SIGINT", () => process.exit(0));
