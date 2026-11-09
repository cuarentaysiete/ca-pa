"""PySide6 front-end for ca-pa.

A three-pane window: a file list on the left, the layer tree in the middle
and a status line at the bottom.  Files arrive by drag-and-drop (or by
command line); each is parsed on a worker thread (so the window never
freezes, even for multi-GB files) and its layer tree is shown without any
pixel data ever being decoded.

Display rules:

* Each layer's colour is painted as the **Name cell's background**; the cell
  text picks black or white against that background so it stays readable in
  both light and dark system themes.
* **Hidden** rows are drawn in attenuated text (blended toward the row
  background, so they still survive dark mode).
* Layers and folders are counted **separately** everywhere.
* Files can be closed individually (the ✕ on the sidebar row, the Close
  toolbar button or the Delete key) or all at once (Close all).
* The application opens with a small splash: the icon on white with the
  app's name.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QObject, Qt, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QColor, QIcon, QPalette, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QSplashScreen,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from . import labels
from .errors import ParseError
from .model import DocumentInfo, LayerNode
from .parser import parse_file

#: file extensions we accept (drag-and-drop and the open dialog).
SUPPORTED_EXTS = {".tif", ".tiff", ".psd", ".psb"}

#: the tree's columns, in order.  Each layer's colour is drawn as the Name
#: cell's background (see :func:`_make_item`), so there is no Colour column.
COLUMNS = ["Name", "Type", "Opacity", "Blend", "Notes"]

#: Fallback attenuated (hidden row) text, used when the palette offers none.
_DIMMED_TEXT_RGB = (140, 140, 140)

#: Neutral fill for folders that carry no colour.
_CONTAINER_RGB = (235, 235, 245)

#: How far hidden-row text is blended toward its background (1.0 = invisible).
_DIM_BLEND = 0.55


def _resource(name: str) -> str:
    """Absolute path of a bundled resource (works from source and from a bundle)."""
    import sys

    base = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.join(base, "resources", name)
    if os.path.exists(candidate):
        return candidate
    # PyInstaller bundles resources next to the executable.
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        candidate = os.path.join(meipass, "resources", name)
        if os.path.exists(candidate):
            return candidate
    return candidate


def app_icon() -> QIcon | None:
    """The application window icon, or None if the resource is missing."""
    path = _resource("ca-pa_icon.png")
    if not os.path.exists(path):
        return None
    icon = QIcon(path)
    return icon if not icon.isNull() else None


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


def _blend(a: QColor, b: QColor, t: float) -> QColor:
    """Linear mix of two colours: ``t=0`` returns *a*, ``t=1`` returns *b*."""
    t = min(1.0, max(0.0, t))
    out = QColor()
    out.setRgb(
        int(a.red() + (b.red() - a.red()) * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue() + (b.blue() - a.blue()) * t),
        a.alpha(),
    )
    return out


def _contrast_fg(bg: QColor) -> QColor:
    """A text colour (near-black or near-white) readable on ``bg``.

    Based on the W3C relative luminance of the background, so the Name cell
    text stays legible no matter the system theme: light swatches get dark
    text, dark swatches get light text, in *both* light and dark mode.
    """
    lum = (
        0.2126 * bg.red() + 0.7152 * bg.green() + 0.0722 * bg.blue()
    ) / 255.0
    if lum > 0.5:
        return QColor(22, 22, 24)
    return QColor(245, 245, 248)


def _dim_colors(tree: QTreeWidget) -> list[QColor]:
    """Attenuated text colours (one per column) for hidden rows.

    Derived from the *active palette*: the theme's text colour blended toward
    the theme's base colour, so "dimmed" means the same thing in light and
    dark themes alike.  Falls back to a fixed mid-grey when the palette
    offers nothing usable.
    """
    pal = tree.palette()
    text = pal.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Text)
    base = pal.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Base)
    if text.isValid() and base.isValid():
        dim = _blend(text, base, _DIM_BLEND)
        return [dim] * len(COLUMNS)
    return [QColor(*_DIMMED_TEXT_RGB)] * len(COLUMNS)


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


def _make_item(layer: LayerNode, dim: list[QColor] | None = None) -> QTreeWidgetItem:
    """Build one tree item for a layer, recursively.

    ``dim`` is the list of attenuated text colours (see :func:`_dim_colors`);
    when omitted a fixed mid-grey is used, which is fine for tests.
    """
    item = QTreeWidgetItem()
    item.setText(0, layer.name)
    item.setText(1, _type_label(layer))
    item.setText(2, "%d%%" % (layer.opacity * 100 // 255))
    item.setText(3, layer.blend_mode or "Normal")
    item.setText(4, _notes(layer))
    if dim is None:
        dim = [QColor(*_DIMMED_TEXT_RGB)] * len(COLUMNS)

    # The Name cell's background carries the layer's colour (inherited from
    # the nearest coloured folder when the layer has none of its own); the
    # cell text is chosen to contrast with that background.
    bg = _swatch(layer.color)
    if bg is None and layer.kind == "group":
        bg = QColor(*_CONTAINER_RGB)  # uncoloured container
    if bg is not None:
        item.setBackground(0, bg)
        item.setForeground(0, _contrast_fg(bg))

    if not layer.visible:
        for col in range(len(COLUMNS)):
            item.setForeground(col, dim[col])
        if bg is not None:
            # Attenuate the Name cell *against its own background*: a fixed
            # grey would vanish on dark swatches in dark mode.
            item.setForeground(0, _blend(_contrast_fg(bg), bg, _DIM_BLEND))

    item.setData(0, Qt.ItemDataRole.UserRole, layer)
    for child in layer.children:
        item.addChild(_make_item(child, dim))
    return item


def populate_tree(tree: QTreeWidget, doc: DocumentInfo) -> None:
    """Fill ``tree`` with the layer tree of ``doc`` (all rows expanded)."""
    tree.clear()
    dim = _dim_colors(tree)
    for layer in doc.layers:
        item = _make_item(layer, dim)
        tree.addTopLevelItem(item)
    tree.expandAll()
    _autosize(tree)


def _autosize(tree: QTreeWidget) -> None:
    header = tree.header()
    header.setSectionResizeMode(QHeaderView.ResizeToContents)
    header.setStretchLastSection(True)


def _make_splash() -> QSplashScreen | None:
    """The start-up splash: the app icon on white, with the app's name.

    Returns None when the icon resource is missing (the app then just opens
    its window).
    """
    path = _resource("ca-pa_icon.png")
    if not os.path.exists(path):
        return None
    img = QPixmap(420, 300)
    img.fill(QColor("white"))
    p = QPainter(img)
    try:
        icon = QIcon(path)
        pix = icon.pixmap(128, 128)
        if not pix.isNull():
            p.drawPixmap((img.width() - 128) // 2, 40, pix)
        font = p.font()
        font.setPointSize(13)
        p.setFont(font)
        p.setPen(QColor(70, 70, 72))
        p.drawText(
            img.rect().adjusted(0, 184, 0, 0),
            Qt.AlignHCenter,
            "ca-pa: a layer inspector",
        )
    finally:
        p.end()
    return QSplashScreen(img)


class _ParseResult(QObject):
    """Carries the (doc, error) signal back to the main thread.

    PySide6 only allows Python-declared signals on QObject subclasses, and
    QRunnable is not exposed as one, so the signal lives on a small companion
    object that the window keeps alive.
    """

    done = Signal(object, str)  # (DocumentInfo | None, error string)


class ParseTask(QRunnable):
    """Parse one file off the main thread, pooled in a :class:`QThreadPool`.

    A :class:`QThread` subclass destroyed while still running aborts the app
    ("QThread: Destroyed while thread is still running"); a pooled runnable
    can be waited on cleanly via ``QThreadPool.waitForDone()`` instead.
    """

    def __init__(self, path: str) -> None:
        super().__init__()
        self.path = path
        self.setAutoDelete(True)
        self.result = _ParseResult()

    def run(self) -> None:
        try:
            self.result.done.emit(parse_file(self.path), "")
        except ParseError as exc:
            self.result.done.emit(None, str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            self.result.done.emit(None, "%s: %s" % (type(exc).__name__, exc))


class MainWindow(QMainWindow):
    """The ca-pa window: a file list, a layer tree and a status line."""

    def __init__(self, files: list[str] | None = None) -> None:
        super().__init__()
        self.setWindowTitle("ca-pa — layer structure")
        icon = app_icon()
        if icon is not None:
            self.setWindowIcon(icon)
        self.resize(1040, 680)
        self._files: list[tuple[str, str]] = []  # (path, "loading"|"loaded"|"error")
        self._docs: dict[str, DocumentInfo] = {}
        self._errors: dict[str, str] = {}
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(min(4, os.cpu_count() or 2))
        self._tasks: list[ParseTask] = []
        self._results: list[_ParseResult] = []  # kept alive for queued signals

        # Toolbar: close the selected file, or every open file.
        toolbar = self.addToolBar("File")
        toolbar.setMovable(False)
        self._close_action = toolbar.addAction("Close")
        self._close_action.setToolTip("Close the selected file (Del)")
        self._close_action.triggered.connect(
            lambda _checked=False: self.close_current()
        )
        self._close_all_action = toolbar.addAction("Close all")
        self._close_all_action.setToolTip("Close every open file")
        self._close_all_action.triggered.connect(
            lambda _checked=False: self.close_all()
        )

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

        self._stack = QStackedWidget()
        self._empty.setStyleSheet("color: #888; font-size: 14px;")
        self._stack.addWidget(self._empty)  # page 0: no file
        self._stack.addWidget(self.tree)  # page 1: the layer tree

        splitter = QSplitter()
        splitter.addWidget(self.sidebar)
        splitter.addWidget(self._stack)
        splitter.setSizes([300, 680])
        self.sidebar.setMinimumWidth(180)
        self.setCentralWidget(splitter)
        self._stack.setCurrentIndex(0)

        for path in files or []:
            self.add_file(path)
        self._show_current()

    # -- file management -----------------------------------------------------

    def add_file(self, path: str) -> None:
        path = os.path.abspath(path)
        if not is_supported(path) or any(p == path for p, _ in self._files):
            return
        task = ParseTask(path)
        task.result.done.connect(
            lambda doc, err, p=path, t=task: self._on_parse_done(p, doc, err, t)
        )
        self._tasks.append(task)
        self._results.append(task.result)
        self._pool.start(task)
        self._files.append((path, "loading"))
        self._refresh_sidebar()
        if len(self._files) == 1:
            self.sidebar.setCurrentRow(0)

    def close_file(self, path: str) -> None:
        """Remove one file (and its document) from the window."""
        if not any(p == path for p, _ in self._files):
            return
        self._files = [(p, s) for p, s in self._files if p != path]
        self._docs.pop(path, None)
        self._errors.pop(path, None)
        for task in [t for t in self._tasks if t.path == path]:
            self._tasks.remove(task)
        self._refresh_sidebar()
        self._show_current()

    def close_current(self) -> None:
        """Close the file selected in the sidebar (toolbar button / Del)."""
        current = self.sidebar.currentItem()
        if current is not None:
            self.close_file(current.data(Qt.ItemDataRole.UserRole))

    def close_all(self) -> None:
        """Close every open file; the window goes back to its empty state."""
        self._files = []
        self._docs.clear()
        self._errors.clear()
        self._tasks = []
        self._refresh_sidebar()
        self._show_current()

    def _on_parse_done(
        self,
        path: str,
        doc: DocumentInfo | None,
        error: str,
        task: ParseTask,
    ) -> None:
        idx = None
        for i, (p, _state) in enumerate(self._files):
            if p == path:
                idx = i
                break
        if idx is None:
            # The file was closed while its parse was still running; drop
            # the result rather than re-adding a ghost entry.
            if task in self._tasks:
                self._tasks.remove(task)
            return
        self._files[idx] = (path, "loaded" if doc is not None else "error")
        if doc is not None:
            self._docs[path] = doc
        else:
            self._errors[path] = error
        if task in self._tasks:
            self._tasks.remove(task)
        self._refresh_sidebar()
        current = self.sidebar.currentItem()
        current_path = current.data(Qt.ItemDataRole.UserRole) if current else None
        if current_path == path:
            self._show_current()
        elif current_path is None and self._files:
            self.sidebar.setCurrentRow(0)
            self._show_current()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Delete:
            self.close_current()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        """Close the window: drop queued parses, let running ones finish.

        Waiting here (instead of killing threads at shutdown) is what fixes
        the "QThread: Destroyed while thread is still running" abort.
        """
        self._pool.clear()
        self._pool.waitForDone()
        event.accept()

    # -- sidebar -------------------------------------------------------------

    def _refresh_sidebar(self) -> None:
        current = self.sidebar.currentItem()
        current_path = current.data(Qt.ItemDataRole.UserRole) if current else None
        self.sidebar.clear()
        for path, state in self._files:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, path)
            widget = self._sidebar_row(path, state)
            item.setSizeHint(widget.sizeHint())
            self.sidebar.addItem(item)
            self.sidebar.setItemWidget(item, widget)
            if path == current_path:
                self.sidebar.setCurrentItem(item)
        if self._files and self.sidebar.currentRow() < 0:
            self.sidebar.setCurrentRow(0)

    def _sidebar_row(self, path: str, state: str) -> QWidget:
        """One sidebar row: the file's label plus an individual ✕ close button."""
        doc = self._docs.get(path)
        error = self._errors.get(path)
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)
        label = QLabel(_row_label(path, state, doc, error))
        label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        button = QPushButton("\u2715")  # ✕
        button.setFixedSize(18, 18)
        button.setCursor(Qt.PointingHandCursor)
        button.setToolTip("Close %s" % os.path.basename(path))
        button.clicked.connect(lambda _checked=False, p=path: self.close_file(p))
        layout.addWidget(label, 1)
        layout.addWidget(button, 0, Qt.AlignRight | Qt.AlignVCenter)
        return widget

    def _on_sidebar_click(self, item: QListWidgetItem) -> None:
        self._show_current()

    # -- display -------------------------------------------------------------

    def _show_current(self) -> None:
        current = self.sidebar.currentItem()
        path = current.data(Qt.ItemDataRole.UserRole) if current else None
        self._close_action.setEnabled(path is not None)
        self._close_all_action.setEnabled(bool(self._files))
        if path and path in self._docs:
            doc = self._docs[path]
            populate_tree(self.tree, doc)
            self._stack.setCurrentWidget(self.tree)
            self._status.showMessage(_status_line(doc))
            return
        self.tree.clear()
        if path and path in self._errors:
            self._stack.setCurrentWidget(self.tree)
            self._status.showMessage(
                "Could not read %s: %s" % (os.path.basename(path), self._errors[path])
            )
        else:
            self._stack.setCurrentWidget(self._empty)
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


def _pl(n: int, word: str) -> str:
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def _row_label(path: str, state: str, doc: DocumentInfo | None, error: str | None) -> str:
    name = os.path.basename(path)
    if state == "loading":
        return "%s  (loading…)" % name
    if state == "error":
        return "%s  (error)" % name
    return "%s  ·  %s · %s ·  %s" % (
        name,
        _pl(doc.layer_count(), "layer"),
        _pl(doc.folder_count(), "folder"),
        _human_mb(doc.size_bytes),
    )


def _status_line(doc: DocumentInfo) -> str:
    bits = [
        "%s  %s" % (os.path.basename(doc.path), _human_mb(doc.size_bytes)),
        "%d×%d px" % (doc.width, doc.height),
        "%s-bit" % doc.bit_depth,
        doc.color_mode,
        "%s · %s" % (_pl(doc.layer_count(), "layer"), _pl(doc.folder_count(), "folder")),
    ]
    line = "   ·   ".join(bits)
    if doc.warnings:
        line += "   ·   ⚠ %s" % "; ".join(doc.warnings)
    return line


def run(files: list[str] | None = None, *, use_splash=True, on_ready=None) -> int:
    """Create the application and window, and enter the event loop.

    ``use_splash`` may be a bool or a zero-argument callable evaluated when
    the splash would be created (the launcher passes a live check on the
    native pre-Qt splash child).  ``on_ready`` is called once, after the
    main window is on screen.
    """
    import os

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    icon = app_icon()
    if icon is not None:
        app.setWindowIcon(icon)

    if callable(use_splash):
        use_splash = use_splash()
    splash = _make_splash() if use_splash else None
    if splash is not None:
        splash.show()
        app.processEvents()
        app.processEvents()  # let the splash actually paint

    window = MainWindow(files or [])
    window.show()
    if on_ready is not None:
        on_ready()
    if splash is not None:
        app.processEvents()
        splash.finish(window)

    # CI smoke hook: close the window shortly after it shows, so a built
    # bundle can be exercised headlessly in the workflow.  Going through
    # window.close() (rather than app.quit()) exercises closeEvent — the
    # thread-pool drain — so the process exits cleanly.
    if os.environ.get("CA_PA_SMOKE"):
        from PySide6.QtCore import QTimer

        QTimer.singleShot(2000, window.close)

    return app.exec()
