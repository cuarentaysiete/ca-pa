"""Shared pytest fixtures.

The GUI tests run on Qt's *offscreen* platform so they work in a headless
container (no X server).  The environment variable must be set before PySide6
is imported anywhere, hence at module-import time here.
"""

from __future__ import annotations

import os

# Must precede any PySide6 import.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def samples(tmp_path_factory):
    """The generated sample files, shared across test modules."""
    from tests import make_samples

    out = tmp_path_factory.mktemp("samples")
    return make_samples.generate_all(str(out))


@pytest.fixture(scope="session")
def qapp():
    """A shared offscreen QApplication."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
    app.quit()
