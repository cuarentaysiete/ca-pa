"""Smoke tests for the PySide6 front-end (run on the offscreen platform)."""

from __future__ import annotations

import os

import pytest

from ca_pa import gui
from ca_pa.gui import MainWindow
from ca_pa.model import LayerNode
from ca_pa.parser import parse_file


def _load(window: MainWindow, path: str) -> None:
    """Synchronously load a file into the window (no worker thread)."""
    from ca_pa.errors import ParseError

    try:
        doc = parse_file(path)
    except ParseError as exc:
        window._errors[path] = str(exc)
        for i, (p, _s) in enumerate(window._files):
            if p == path:
                window._files[i] = (path, "error")
        return
    window._docs[path] = doc
    for i, (p, _s) in enumerate(window._files):
        if p == path:
            window._files[i] = (path, "loaded")
    window._refresh_sidebar()
    window._show_current()


def test_is_supported():
    assert gui.is_supported("a.tif")
    assert gui.is_supported("b.TIFF")
    assert gui.is_supported("c.psd")
    assert gui.is_supported("d.psb")
    assert not gui.is_supported("e.png")
    assert not gui.is_supported("f.txt")


def test_type_label_and_notes():
    adj = LayerNode(name="x", kind="adjustment", kind_label="Adjustment", adjustment="Curves")
    assert gui._type_label(adj) == "Adjustment: Curves"
    pix = LayerNode(name="y", kind="pixel", opacity=128, blend_mode="Multiply", visible=False, has_vector_mask=True)
    assert "hidden" in gui._notes(pix)
    assert "vector mask" in gui._notes(pix)


def test_window_constructs(qapp, samples):
    window = MainWindow()
    assert window.tree.columnCount() == len(gui.COLUMNS)
    assert window.sidebar.count() == 0
    window.close()


def test_window_shows_tree(qapp, samples):
    window = MainWindow()
    path = samples["sample.psd"]
    window._files.append((os.path.abspath(path), "loading"))
    window._refresh_sidebar()
    _load(window, path)
    doc = parse_file(path)
    assert window.tree.topLevelItemCount() == len(doc.layers)
    assert "layer" in window.statusBar().currentMessage().lower()
    window.close()


def test_window_shows_error(qapp):
    window = MainWindow()
    window._files.append(("/no/such/file.psd", "error"))
    window._errors["/no/such/file.psd"] = "boom"
    window._refresh_sidebar()
    window.sidebar.setCurrentRow(0)
    window._show_current()
    assert window.tree.topLevelItemCount() == 0
    assert "Could not read" in window.statusBar().currentMessage()
    window.close()
