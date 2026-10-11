"""Directory memory-provider shim: expose MagicContextMemoryProvider under the
``magic_context`` provider name Hermes' memory loader discovers by directory.

The pm-era runtime (hermes-agent v0.21.6+59) discovers non-core memory providers
by scanning ``~/.hermes/plugins/<provider-name>/`` for an ``__init__.py`` whose
text mentions the MemoryProvider contract, or via the
``hermes_agent.memory_providers`` entry point in the process's own
site-packages. The running gateway predates the pm generation that carries the
magic-hermes editable install, so only the directory route can reach it.
Mirrors the plugin-root ``__init__.py`` shim (fix-queue item 246, 2026-10-11).
"""

try:
    from magic_hermes.memory_provider import MagicContextMemoryProvider as MemoryProvider  # noqa: F401
except ImportError:  # directory load: make the repo's src/ importable, then retry
    import sys
    from pathlib import Path

    _src = Path(__file__).resolve().parent.parent / "src"
    if _src.is_dir() and str(_src) not in sys.path:
        sys.path.insert(0, str(_src))
    from magic_hermes.memory_provider import MagicContextMemoryProvider as MemoryProvider  # noqa: F401

__all__ = ["MemoryProvider"]
