# Hermes ↔ Pi ↔ OpenCode: Intentional Divergences

Modeled on `packages/pi-plugin/PARITY.md` upstream. Records **deliberate**
differences between magic-hermes and the pi/opencode plugins. "Same effective
behavior, different mechanism where the host runtimes differ" is the rule.

All three implementations share one SQLite DB (`~/.local/share/cortexkit/
magic-context/`) and the same subc daemon.

---

## 1. Language and transport

**Pi/OpenCode:** TypeScript plugins, in-process core (TS) or Rust mode via
subc.

**Hermes:** Python plugin. There is no in-process TS option, so **every**
transform/store/tokenize call goes through the subc daemon (the path pi/
opencode call "rust mode"). magic-hermes has no reduced-transport mode to
choose; daemon mode is the only mode.

## 2. Subagent execution model

**Pi:** historian/dreamer/sidekick run as separate `pi --print` processes
loading a lean entry file; recursion guard prevents them reaching the
context pipeline.

**Hermes:** TBD (outstanding question #2 in the plan) — hermes has
`register_auxiliary_task` and its own agent runtime; the hermes-native
mechanism is preferred over subprocess spawning.

## 3. Context surface

**Pi/OpenCode:** context arrives via the harness's message-transform hooks.

**Hermes:** the plugin registers a `ContextEngine` (replacing hermes' built-in
`ContextCompressor` — hermes allows exactly one). Compaction therefore flows
through hermes' native compression seam, not a parallel pipeline.

## 4. Memory surface

**Pi/OpenCode:** plugin-managed memory config.

**Hermes:** hermes' standard memory config surface is the authority;
magic-hermes feeds the shared store but surfaces toggles through hermes
config keys.

---

*(Extend as implementation reveals further divergences. Do not delete entries;
supersede in place.)*
