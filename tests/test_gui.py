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


def test_columns_deduplicate_colour_into_name(qapp):
    """The Colour column is gone; colour lives in the Name cell background."""
    assert gui.COLUMNS == ["Name", "Type", "Opacity", "Blend", "Notes"]
    assert "Colour" not in gui.COLUMNS


def test_make_item_paints_name_and_attenuates_hidden(qapp):
    from ca_pa.gui import _make_item
    from ca_pa.model import LayerNode

    # A coloured layer paints its colour into the Name cell (column 0).
    colored = LayerNode(name="c", kind="group", kind_label="Folder", color="#e78679")
    item = _make_item(colored)
    assert item.text(0) == "c"
    assert item.background(0).color().name().lower() == "#e78679"

    # An uncoloured folder falls back to the neutral container fill.
    plain = LayerNode(name="f", kind="group", kind_label="Folder")
    assert _make_item(plain).background(0).color().name().lower() == "#ebebf5"

    # A hidden row gets attenuated (non-black) text on every column.
    hidden = LayerNode(name="h", kind="pixel", visible=False)
    hi = _make_item(hidden)
    for col in range(len(gui.COLUMNS)):
        c = hi.foreground(col).color()
        assert c.isValid(), col
        assert c.name().lower() != "#000000", (col, c.name())


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


def test_close_while_parsing_is_safe(qapp, samples):
    """Closing the window mid-parse must not abort (no live QThread)."""
    import time

    window = MainWindow()
    path = os.path.abspath(samples["sample.psd"])
    window.add_file(path)
    window.close()  # closeEvent must wait for the pool, never kill a thread
    deadline = time.time() + 10
    while time.time() < deadline:
        qapp.processEvents()
        states = [s for p, s in window._files if p == path]
        if states and states[0] != "loading":
            break
        time.sleep(0.01)
    assert window._pool.activeThreadCount() == 0
    assert any(s == "loaded" for _, s in window._files if True)
    window.deleteLater()
    qapp.processEvents()
