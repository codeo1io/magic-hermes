"""Directory-plugin shim for Magic-Hermes.

Hermes directory plugins load ``__init__.py`` from the plugin root. The real
package lives in ``src/magic_hermes`` so the same code also works when
installed through the ``hermes_agent.plugins`` entry point (pm workspace
member or pip). Without this shim the directory source cannot load and the
gateway silently falls back to the built-in compressor ("Context engine
'magic-context' not found") — fix-queue item 246, 2026-10-11.
"""

try:
    from magic_hermes.plugin import register  # entry-point / workspace-member install
except ImportError:  # directory-plugin load: make src/ importable, then retry
    import sys
    from pathlib import Path

    _src = Path(__file__).resolve().parent / "src"
    if _src.is_dir() and str(_src) not in sys.path:
        sys.path.insert(0, str(_src))
    from magic_hermes.plugin import register

__all__ = ["register"]
