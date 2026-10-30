"""Standalone launcher used by the PyInstaller bundle.

Kept separate from ``__main__`` so the frozen app has a single, explicit
entry point that just opens the window.
"""

from __future__ import annotations

import sys

from ca_pa import gui


def main() -> int:
    return gui.run()


if __name__ == "__main__":
    sys.exit(main())
