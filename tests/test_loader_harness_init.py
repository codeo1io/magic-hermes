# Regression: 2026-10-12 Magic Context 0.48.0 adoption (fix-queue item 259).
#
# 0.48.0 is esbuild-bundled with the harness initializer RENAMED
# (observed: setHarness2(PI_HARNESS_KIND);) — the loader's literal-form
# match failed with "Pi harness initializer not found", failing the bind
# for all 49 runtime-integration tests. This test pins the load() seam
# directly: a synthetic adapter carrying the renamed form must load via
# the loader and declare harness "hermes"; a foreign dynamic-arg form
# must stay fail-closed.

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parents[1] / "src" / "magic_hermes" / "bridge"
LOADER = BRIDGE_DIR / "loader.mjs"

# Drives the loader the way runtime.py does: module.register() installs
# the hooks into the pipeline, then a plain import of the adapter URL
# goes through them. MAGIC_HERMES_ADAPTER_URL names the adapter (bare
# URL — the loader compares it against url.split("?")[0]); the import
# adds a cache-buster so repeat runs never hit a compiled cache.
_DRIVER = r"""
import { register } from "node:module";
import { pathToFileURL } from "node:url";
// Env BEFORE register: module.register() hooks run on their own thread
// with a snapshot of the environment — setting MAGIC_HERMES_ADAPTER_URL
// after register() is invisible to the load hook (verified live; also
// matches runtime.py's production order).
const bare = pathToFileURL(process.argv[3]).href;
process.env.MAGIC_HERMES_ADAPTER_URL = bare;
register(pathToFileURL(process.argv[2]).href);
const adapterUrl = bare + "?v=" + Date.now();
const mod = await import(adapterUrl);
console.log(JSON.stringify({ harness: mod.getHarness() }));
"""


def _exposed_names() -> list[str]:
    """Parse the EXPOSED list out of loader.mjs so the synthetic adapter
    always satisfies the full required-export contract even as the list
    evolves."""
    src = LOADER.read_text(encoding="utf-8")
    m = re.search(r"const EXPOSED = \[(.*?)\];", src, re.DOTALL)
    assert m is not None, "EXPOSED list not found in loader.mjs"
    return re.findall(r'"([A-Za-z_$][\w$]*)"', m.group(1))


def _adapter_source(initializer_line: str) -> str:
    """A synthetic adapter that (a) defines every EXPOSED name (the
    loader's fail-closed contract demands them all) and (b) carries the
    given harness-initializer line, verbatim, as 0.48-style bundle tail."""
    parts = [
        "let current = 'pi';",
        "export function setHarness2(v) { current = v; }",
        "export function getHarness() { return current; }",
    ]
    for name in _exposed_names():
        if name.endswith("SYSTEM_PROMPT"):
            parts.append(f"export const {name} = null;")
        else:
            parts.append(f"export function {name}() {{ return null; }}")
    parts.append("var PI_HARNESS_KIND = 'pi';")
    # The initializer must run AFTER setHarness2 is defined and reference
    # the detection constant declared above — bundle-tail position, as in
    # the real 0.48 dist/index.js.
    parts.append(initializer_line)
    return "\n".join(parts) + "\n"


_RENAMED = "setHarness2(PI_HARNESS_KIND);"
_LEGACY = 'setHarness("pi");'
_DYNAMIC = "setHarness2(computeKind());"


def _run_loader(tmp_path: Path, adapter_source: str) -> tuple[int, dict | str]:
    adapter = tmp_path / "adapter.mjs"
    adapter.write_text(adapter_source, encoding="utf-8")
    driver = tmp_path / "driver.mjs"
    driver.write_text(_DRIVER, encoding="utf-8")
    proc = subprocess.run(
        [os.environ.get("NODE_BIN", "node"), str(driver), str(LOADER), str(adapter)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        cwd=str(tmp_path),
    )
    if proc.returncode != 0:
        return proc.returncode, (proc.stderr or proc.stdout).strip()[-400:]
    try:
        return 0, json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return 1, f"unparseable driver output: {proc.stdout!r} {proc.stderr!r}"


def test_loader_accepts_renamed_harness_initializer(tmp_path):
    """0.48.x shape: callee renamed, kind is the detection constant.

    OLD code fails with "Pi harness initializer not found"; NEW code
    rewrites to setHarness2("hermes") preserving the callee.
    """
    rc, out = _run_loader(tmp_path, _adapter_source(_RENAMED))
    assert rc == 0, out
    assert out["harness"] == "hermes", out


def test_loader_still_accepts_legacy_harness_initializer(tmp_path):
    """0.47.x-and-earlier shape: verbatim setHarness("pi"); stays green."""
    src = _adapter_source(_LEGACY).replace(
        "export function setHarness2(v) { current = v; }",
        "export function setHarness(v) { current = v; }",
    )
    rc, out = _run_loader(tmp_path, src)
    assert rc == 0, out
    assert out["harness"] == "hermes", out


def test_loader_fail_closed_on_dynamic_harness_kind(tmp_path):
    """A dynamic kind argument is NOT one of the sanctioned forms: the
    loader must keep refusing rather than guess (fail-closed contract)."""
    src = _adapter_source(_DYNAMIC) + "function computeKind() { return 'pi'; }\n"
    rc, out = _run_loader(tmp_path, src)
    assert rc != 0
    assert "Pi harness initializer not found" in out


def test_loader_fail_closed_on_missing_initializer(tmp_path):
    src = _adapter_source("export function nothing() {}")
    rc, out = _run_loader(tmp_path, src)
    assert rc != 0
    assert "Pi harness initializer not found" in out
