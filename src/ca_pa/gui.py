"""PySide6 front-end for ca-pa.

A three-pane window: a file list on the left, the layer tree in the middle
and a status line at the bottom.  Files arrive by drag-and-drop; each is
parsed on a worker thread (so the window never freezes, even for multi-GB
files) and its layer tree is shown without any pixel data ever being decoded.
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QSplitter,
    QStatusBar,
    QTreeWidget,
    QTreeWidgetItem,
)

from . import labels
from .errors import ParseError
from .model import DocumentInfo, LayerNode
from .parser import parse_file

#: file extensions we accept (drag-and-drop and the open dialog).
SUPPORTED_EXTS = {".tif", ".tiff", ".psd", ".psb"}

#: the tree's columns, in order.
COLUMNS = ["Name", "Type", "Colour", "Opacity", "Blend", "Notes"]


def is_supported(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in SUPPORTED_EXTS


def _swatch(hexstr: str | None) -> QColor | None:
    """A QColor for a ``#rrggbb`` string, or None."""
    if not hexstr:
        return None
    try:
        c = QColor(hexstr)
        return c if c.isValid() else None
    except Exception:
        return None


def _human_mb(n: int) -> str:
    return "%.1f MB" % (n / 1_000_000)


def _type_label(layer: LayerNode) -> str:
    if layer.kind == "adjustment" and layer.adjustment:
        return "Adjustment: " + layer.adjustment
    return layer.kind_label


def _notes(layer: LayerNode) -> str:
    notes = []
    if not layer.visible:
        notes.append("hidden")
    if layer.has_vector_mask:
        notes.append("vector mask")
    if layer.has_alpha_mask:
        notes.append("alpha mask")
    if layer.effects:
        notes.append("fx: " + ", ".join(layer.effects))
    return "  ".join(notes)


class ParseWorker(QThread):
    """Parse one file off the main thread; emit (doc, error) when done."""

    done = Signal(object, str)  # (DocumentInfo | None, error string)

    def __init__(self, path: str, parent=None) -> None:
        super().__init__(parent)
        self.path = path

    def run(self) -> None:
        try:
            self.done.emit(parse_file(self.path), "")
        except ParseError as exc:
            self.done.emit(None, str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            self.done.emit(None, "%s: %s" % (type(exc).__name__, exc))


def _make_item(layer: LayerNode) -> QTreeWidgetItem:
    """Build one tree item for a layer, recursively."""
    item = QTreeWidgetItem()
    item.setText(0, layer.name)
    item.setText(1, _type_label(layer))
    item.setText(3, "%d%%" % (layer.opacity * 100 // 255))
    item.setText(4, layer.blend_mode or "Normal")
    item.setText(5, _notes(layer))
    color = _swatch(layer.color)
    if color is not None:
        item.setBackground(2, color)
    elif layer.color:
        item.setText(2, layer.color)
    if layer.kind == "folder":
        # folders get a subtle fill so they read as containers
        item.setBackground(0, QColor(235, 235, 245))
    item.setData(0, Qt.ItemDataRole.UserRole, layer)
    for child in layer.children:
        item.addChild(_make_item(child))
    return item


def populate_tree(tree: QTreeWidget, doc: DocumentInfo) -> None:
    """Fill ``tree`` with the layer tree of ``doc`` (top level only expanded)."""
    tree.clear()
    for layer in doc.layers:
        item = _make_item(layer)
        # keep groups collapsed by default; the user opens them
        item.setExpanded(False)
        tree.addTopLevelItem(item)
    tree.expandAll()
    _autosize(tree)


def _autosize(tree: QTreeWidget) -> None:
    header = tree.header()
    header.setSectionResizeMode(QHeaderView.ResizeToContents)
    header.setStretchLastSection(True)


class MainWindow(QMainWindow):
    """The ca-pa window: a file list, a layer tree and a status line."""

    def __init__(self, files: list[str] | None = None) -> None:
        super().__init__()
        self.setWindowTitle("ca-pa — layer structure")
        self.resize(1040, 680)
        self._files: list[tuple[str, str]] = []  # (path, "loading"|"loaded"|"error")
        self._docs: dict[str, DocumentInfo] = {}
        self._errors: dict[str, str] = {}
        self._workers: list[ParseWorker] = []

        self.sidebar = QListWidget()
        self.sidebar.setDragDropMode(QAbstractItemView.NoDragDrop)
        self.sidebar.itemClicked.connect(self._on_sidebar_click)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(COLUMNS))
        self.tree.setHeaderLabels(COLUMNS)
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self._empty = QLabel("Drag & drop .tif / .tiff / .psd / .psb files here")
        self._empty.setAlignment(Qt.AlignCenter)

        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("No file open — drop a layered image to begin")

        self.setAcceptDrops(True)

        for path in files or []:
            self.add_file(path)
        self._show_current()

    # -- file management -----------------------------------------------------

    def add_file(self, path: str) -> None:
        path = os.path.abspath(path)
        if not is_supported(path) or any(p == path for p, _ in self._files):
            return
        worker = ParseWorker(path, self)
        worker.done.connect(lambda doc, err: self._on_worker_done(worker, doc, err))
        worker.start()
        self._workers.append(worker)
        self._files.append((path, "loading"))
        self._refresh_sidebar()
        if len(self._files) == 1:
            self.sidebar.setCurrentRow(0)

    def _on_worker_done(self, worker: ParseWorker, doc: DocumentInfo | None, error: str) -> None:
        path = worker.path
        for i, (p, _state) in enumerate(self._files):
            if p == path:
                self._files[i] = (path, "loaded" if doc is not None else "error")
        if doc is not None:
            self._docs[path] = doc
        else:
            self._errors[path] = error
        self._workers = [w for w in self._workers if w.isRunning()]
        self._refresh_sidebar()
        current = self.sidebar.currentItem()
        current_path = current.data(Qt.ItemDataRole.UserRole) if current else None
        if current_path == path:
            self._show_current()
        elif current_path is None and self._files:
            self.sidebar.setCurrentRow(0)
            self._show_current()

    # -- sidebar -------------------------------------------------------------

    def _refresh_sidebar(self) -> None:
        current = self.sidebar.currentItem()
        current_path = current.data(Qt.ItemDataRole.UserRole) if current else None
        self.sidebar.clear()
        for path, state in self._files:
            item = QListWidgetItem(
                _row_label(path, state, self._docs.get(path), self._errors.get(path))
            )
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.sidebar.addItem(item)
            if path == current_path:
                self.sidebar.setCurrentItem(item)
        if self._files and self.sidebar.currentRow() < 0:
            self.sidebar.setCurrentRow(0)

    def _on_sidebar_click(self, item: QListWidgetItem) -> None:
        self._show_current()

    # -- display -------------------------------------------------------------

    def _show_current(self) -> None:
        current = self.sidebar.currentItem()
        path = current.data(Qt.ItemDataRole.UserRole) if current else None
        if path and path in self._docs:
            doc = self._docs[path]
            populate_tree(self.tree, doc)
            self._status.showMessage(_status_line(doc))
            return
        self.tree.clear()
        if path and path in self._errors:
            self._status.showMessage(
                "Could not read %s: %s" % (os.path.basename(path), self._errors[path])
            )
        else:
            self._status.showMessage("No file open — drop a layered image to begin")

    # -- drag & drop ---------------------------------------------------------

    def _dropped_paths(self, mime) -> list[str]:
        paths = []
        for url in mime.urls():
            p = url.toLocalFile()
            if is_supported(p) and p not in paths:
                paths.append(p)
        return paths

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls() and any(
            is_supported(u.toLocalFile()) for u in event.mimeData().urls()
        ):
            event.acceptProposedAction()

    dragMoveEvent = dragEnterEvent

    def dropEvent(self, event) -> None:
        paths = self._dropped_paths(event.mimeData())
        if not paths:
            return
        event.acceptProposedAction()
        for path in paths:
            self.add_file(path)


# -- small label helpers -----------------------------------------------------


def _row_label(path: str, state: str, doc: DocumentInfo | None, error: str | None) -> str:
    name = os.path.basename(path)
    if state == "loading":
        return "%s  (loading…)" % name
    if state == "error":
        return "%s  (error)" % name
    return "%s  ·  %d layers  ·  %s" % (name, doc.layer_count(), _human_mb(doc.size_bytes))


def _status_line(doc: DocumentInfo) -> str:
    bits = [
        "%s  %s" % (os.path.basename(doc.path), _human_mb(doc.size_bytes)),
        "%d×%d px" % (doc.width, doc.height),
        "%s-bit" % doc.bit_depth,
        doc.color_mode,
        "%d layers" % doc.layer_count(),
    ]
    line = "   ·   ".join(bits)
    if doc.warnings:
        line += "   ·   ⚠ %s" % "; ".join(doc.warnings)
    return line


def run(files: list[str] | None = None) -> int:
    """Create the application and window, and enter the event loop."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    window = MainWindow(files or [])
    window.show()
    return app.exec()
