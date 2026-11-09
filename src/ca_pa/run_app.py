"""Standalone launcher used by the PyInstaller bundle (and the dev GUI).

Kept separate from ``__main__`` so the frozen app has a single, explicit
entry point that just opens the window.

Startup order — the interesting part:

1. If re-run with ``--splash-child``, draw the *native* pre-Qt splash
   (see :mod:`ca_pa.bootstrap`) and keep it up until the parent dies.
   The parent spawns this child **before** importing Qt, so a window is
   on screen from the very first moment of the start.
2. Otherwise: spawn the splash child (best effort), *then* import Qt and
   build the window — that is the slow part (the Qt libraries, the
   platform plugin, the font database) — and kill the child the moment
   the real window is up.

Set ``CA_PA_NO_SPLASH=1`` to skip the native child (the in-window Qt
splash then covers the wait).
"""

from __future__ import annotations

import os
import sys

from ca_pa import bootstrap

_SPLASH_CHILD_FLAG = "--splash-child"


def _child_main() -> int:
    """The splash child: no Qt, ever."""
    return bootstrap.run_native_splash()


def main() -> int:
    if _SPLASH_CHILD_FLAG in sys.argv[1:]:
        return _child_main()

    splash = bootstrap.spawn_splash()

    def _on_ready() -> None:
        # The real window is on screen: the native splash has done its job.
        bootstrap.terminate_splash(splash)

    code = 0
    try:
        # Imported *after* the splash child is up: this is what takes
        # seconds in the frozen app.
        from ca_pa import gui

        # The in-window Qt splash is only the fallback for platforms/environments
        # where the native child could not start (Linux, headless, ...).
        code = gui.run(use_splash=lambda: not bootstrap.is_alive(splash), on_ready=_on_ready)
    finally:
        bootstrap.terminate_splash(splash)
    return code


if __name__ == "__main__":
    sys.exit(main())
