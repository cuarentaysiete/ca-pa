"""The pre-Qt splash, drawn by a throwaway child process.

The frozen app spends several seconds before it can draw anything with Qt:
the PyInstaller layout, importing the Qt libraries, and ``QApplication``
initialisation (platform plugin, font database) all happen before a single
pixel is on screen.  A Qt splash created *after* that would only flash at
the very end.

So the parent process spawns a child (itself, run with
``--splash-child``) **before** importing Qt.  The child draws a minimal
*native* splash — a Win32 window on Windows, a Cocoa window on macOS, via
``ctypes`` and nothing else — and keeps it up until the parent dies.  The
parent kills the child the moment the real window is on screen.

Everything here is best-effort: any failure exits silently with status 0,
so the app behaves exactly as before in environments where a native splash
cannot be shown (Linux, headless sessions, ...).  Set ``CA_PA_NO_SPLASH=1``
to disable the child entirely; the in-window Qt splash then covers the
wait.  ``CA_PA_SPLASH_TIMEOUT`` (seconds) overrides the child's built-in
lifetime cap (used by the tests).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

_SPLASH_CHILD_FLAG = "--splash-child"

#: Hard cap on how long the splash child lives if the parent never kills it.
CHILD_TIMEOUT_SECONDS = 120.0

SPLASH_WIDTH = 420
SPLASH_HEIGHT = 300
_TITLE = "ca-pa"
_SUBTITLE = "a layer inspector"


def _child_timeout() -> float:
    try:
        return max(0.1, float(os.environ.get("CA_PA_SPLASH_TIMEOUT", CHILD_TIMEOUT_SECONDS)))
    except ValueError:
        return CHILD_TIMEOUT_SECONDS


def _find_resources() -> str:
    """The bundled ``resources/`` directory (icon PNG/ICO live there)."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [os.path.join(here, "resources")]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(os.path.join(meipass, "ca_pa", "resources"))
    for cand in candidates:
        if os.path.isdir(cand):
            return cand
    return candidates[0]


# ---------------------------------------------------------------------------
# Parent side
# ---------------------------------------------------------------------------


def spawn_splash() -> subprocess.Popen | None:
    """Start the native splash child, best effort.

    Returns the child process, or ``None`` when there is no native splash
    on this platform, when ``CA_PA_NO_SPLASH`` is set, or when spawning
    fails.  The parent must call :func:`terminate_splash` with the result
    (``None`` is fine) once the real window is on screen.
    """
    if os.environ.get("CA_PA_NO_SPLASH"):
        return None
    if sys.platform not in ("win32", "darwin"):
        return None
    if getattr(sys, "frozen", False):
        # The child is the app itself, re-run; the bootloader hands the
        # flag to this very module.
        cmd = [sys.executable, _SPLASH_CHILD_FLAG]
    else:
        cmd = [sys.executable, "-m", "ca_pa.run_app", _SPLASH_CHILD_FLAG]
    try:
        kwargs: dict = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
        if sys.platform == "win32":
            kwargs["creation_flags"] = 0x08000000  # CREATE_NO_WINDOW
        return subprocess.Popen(cmd, **kwargs)
    except Exception:
        return None


def is_alive(proc: subprocess.Popen | None) -> bool:
    """True if *proc* is a running splash child."""
    if proc is None:
        return False
    try:
        return proc.poll() is None
    except Exception:
        return False


def terminate_splash(proc: subprocess.Popen | None) -> None:
    """Kill the splash child (idempotent, never raises)."""
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Child side
# ---------------------------------------------------------------------------


def run_native_splash() -> int:
    """Child entry point: draw the native splash until the parent dies.

    Never raises; always exits 0.  The parent's death is the normal "done"
    signal, with the timeout as a safety net.
    """
    try:
        timeout = _child_timeout()
        resources = _find_resources()
        if sys.platform == "win32":
            _run_splash_windows(resources, timeout)
        elif sys.platform == "darwin":
            _run_splash_macos(resources, timeout)
        # Other platforms: nothing to draw; exit at once (the parent never
        # spawns a child there anyway).
    except Exception:
        pass
    return 0


# ---------------------------------------------------------------------------
# Windows: a borderless white window with the icon and the wordmark.
# ---------------------------------------------------------------------------


def _run_splash_windows(resources: str, timeout: float) -> None:
    import ctypes
    from ctypes import wintypes

    u32 = ctypes.windll.user32
    k32 = ctypes.windll.kernel32
    gdi = ctypes.windll.gdi32

    # Watch the parent: when it dies, we are done.
    parent_pid = k32.GetParentProcessId()
    parent_h = None
    try:
        parent_h = k32.OpenProcess(0x0010 | 0x0400, False, parent_pid)  # SYNC | QUERY
    except Exception:
        parent_h = None

    WM_DESTROY, WM_QUIT = 0x0002, 0x0012
    WNDPROC = ctypes.WINFUNCTYPE(
        ctypes.c_longlong, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    )

    def _wndproc(hwnd, msg, wparam, lparam):
        if msg == WM_DESTROY:
            u32.PostQuitMessage(0)
        return u32.DefWindowProcW(hwnd, msg, wparam, lparam)

    wndproc = WNDPROC(_wndproc)  # keep a reference: ctypes would GC the callback

    class _WNDCLASSEX(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.UINT),
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
            ("hIconSm", wintypes.HICON),
        ]

    class_name = "CAPA_SPLASH"
    wc = _WNDCLASSEX()
    wc.cbSize = ctypes.sizeof(_WNDCLASSEX)
    wc.lpfnWndProc = wndproc
    wc.hInstance = k32.GetModuleHandleW(None)
    wc.hCursor = u32.LoadCursorW(None, 32512)  # IDC_ARROW
    wc.hbrBackground = gdi.CreateSolidBrush(0x00FFFFFF)  # white (BGR0)
    wc.lpszClassName = class_name
    u32.RegisterClassExW(ctypes.byref(wc))

    # Center on the primary work area.
    sw, sh = u32.GetSystemMetrics(0), u32.GetSystemMetrics(1)
    x = (sw - SPLASH_WIDTH) // 2
    y = (sh - SPLASH_HEIGHT) // 2
    hwnd = u32.CreateWindowExW(
        0, class_name, _TITLE,
        0x80000000 | 0x10000000,  # WS_POPUP | WS_VISIBLE
        x, y, SPLASH_WIDTH, SPLASH_HEIGHT,
        None, None, wc.hInstance, None,
    )
    if not hwnd:
        return

    def _add_static(text: str, font_px: int, x: int, y: int, w: int, h: int) -> None:
        font = gdi.CreateFontW(-font_px, 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 0x40, text)
        static = u32.CreateWindowExW(
            0, "STATIC", text, 0x50000002,  # WS_CHILD|VISIBLE | SS_CENTER
            x, y, w, h, hwnd, None, wc.hInstance, None,
        )
        if static:
            u32.SendMessageW(static, 0x0308, font, 1)  # WM_SETFONT, redo

    icon_handle = 0
    ico = os.path.join(resources, "ca-pa_icon.ico")
    if os.path.exists(ico):
        icon_handle = u32.LoadImageW(None, ico, 1, 0, 0) or 0  # IMAGE_ICON

    if icon_handle:
        icon_static = u32.CreateWindowExW(
            0, "STATIC", None, 0x50000003,  # ... | SS_ICON
            (SPLASH_WIDTH - 96) // 2, 48, 96, 96, hwnd, None, wc.hInstance, None,
        )
        if icon_static:
            u32.SendMessageW(icon_static, 0x0170, 1, icon_handle)  # STM_SETICON
        _add_static(_TITLE, 26, 0, 156, SPLASH_WIDTH, 34)
    else:
        _add_static(_TITLE, 34, 0, 108, SPLASH_WIDTH, 44)
    _add_static(_SUBTITLE, 14, 0, 196, SPLASH_WIDTH, 22)

    msg = wintypes.MSG()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        got = 0
        while u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            u32.TranslateMessage(ctypes.byref(msg))
            u32.DispatchMessageW(ctypes.byref(msg))
            got = 1
            if msg.message == WM_QUIT:
                return
        if not got:
            k32.Sleep(15)
        if parent_h and k32.WaitForSingleObject(parent_h, 0) == 0x0000:
            return  # parent gone -> done
    u32.DestroyWindow(hwnd)


# ---------------------------------------------------------------------------
# macOS: a borderless white window (Cocoa via ctypes).
# ---------------------------------------------------------------------------


def _run_splash_macos(resources: str, timeout: float) -> None:
    import ctypes
    from ctypes import c_char_p, c_double, c_int, c_long, c_void_p, Structure

    def _framework(name: str) -> ctypes.CDLL:
        return ctypes.CDLL(f"/System/Library/Frameworks/{name}.framework/{name}")

    objc = ctypes.CDLL("/usr/lib/libobjc.dylib")
    cf = _framework("CoreFoundation")
    ns = _framework("AppKit")  # noqa: F841 (symbols are reached via the objc runtime)

    objc.objc_getClass.restype = c_void_p
    objc.objc_getClass.argtypes = [c_char_p]
    objc.sel_registerName.restype = c_void_p
    objc.sel_registerName.argtypes = [c_char_p]
    _msgsend = objc.objc_msgSend

    def _get(class_name: str) -> int:
        return objc.objc_getClass(class_name.encode())

    def _send(obj, selname: str, sig: list, args: list, restype=c_void_p):
        _msgsend.restype = restype
        _msgsend.argtypes = [c_void_p, c_void_p] + sig
        return _msgsend(obj, objc.sel_registerName(selname.encode()), *args)

    class _NSPoint(Structure):
        _fields_ = [("x", c_double), ("y", c_double)]

    class _NSSize(Structure):
        _fields_ = [("w", c_double), ("h", c_double)]

    class _NSRect(Structure):
        _fields_ = [("origin", _NSPoint), ("size", _NSSize)]

    def _rect(x, y, w, h) -> _NSRect:
        r = _NSRect()
        r.origin.x, r.origin.y = x, y
        r.size.w, r.size.h = w, h
        return r

    def _nsstr(text: str) -> int:
        return _send(_get("NSString"), "stringWithString:", [c_char_p], [text.encode("utf-8")])

    nsapp = _send(_get("NSApplication"), "sharedApplication")
    _send(nsapp, "setActivationPolicy:", [c_int], [1])  # accessory: no dock icon
    _send(nsapp, "finishLaunching")

    # Screen frame, to center the splash.
    screen = _send(_send(nsapp, "screens"), "objectAtIndex:", [c_long], [0])
    try:
        sframe = _send(screen, "frame", [], restype=_NSRect)
    except Exception:
        sframe = None
    sw = sframe.size.w if sframe else 1440
    sh = sframe.size.h if sframe else 900
    ox = sframe.origin.x if sframe else 0
    oy = sframe.origin.y if sframe else 0

    content = _rect(ox + (sw - SPLASH_WIDTH) / 2, oy + (sh - SPLASH_HEIGHT) / 2,
                    SPLASH_WIDTH, SPLASH_HEIGHT)
    win = _send(
        _send(_get("NSWindow"), "alloc"),
        "initWithContentRect:styleMask:backing:defer:",
        [_NSRect, c_long, c_long, c_int],
        [content, 0, 2, 0],  # borderless, buffered, display now
    )
    white = _send(_get("NSColor"), "whiteColor")
    _send(win, "setOpaque:", [c_int], [1])
    _send(win, "setBackgroundColor:", [c_void_p], [white])
    _send(win, "setLevel:", [c_long], [3])  # floating: above the normal windows
    cv = _send(win, "contentView")

    def _label(text: str, size: float, white_level: float, x, y, w, h) -> None:
        tf = _send(_send(_get("NSTextField"), "alloc"), "init")
        color = _send(_get("NSColor"), "colorWithWhite:alpha:", [c_double, c_double],
                      [white_level, 1.0])
        _send(tf, "setStringValue:", [c_void_p], [_nsstr(text)])
        _send(tf, "setFont:", [c_void_p],
              [_send(_get("NSFont"), "systemFontOfSize:", [c_double], [size])])
        _send(tf, "setTextColor:", [c_void_p], [color])
        _send(tf, "setAlignment:", [c_long], [1])  # NSTextAlignmentCenter
        _send(tf, "setBezeled:", [c_int], [0])
        _send(tf, "setEditable:", [c_int], [0])
        _send(tf, "setSelectable:", [c_int], [0])
        _send(tf, "setDrawsBackground:", [c_int], [0])
        _send(tf, "setFrame:", [_NSRect], [_rect(x, y, w, h)])
        _send(cv, "addSubview:", [c_void_p], [tf])

    png = os.path.join(resources, "ca-pa_icon.png")
    icon_img = 0
    if os.path.exists(png):
        icon_img = _send(_send(_get("NSImage"), "alloc"),
                         "initWithContentsOfFile:", [c_void_p], [_nsstr(png)])
    if icon_img:
        iv = _send(_send(_get("NSImageView"), "alloc"), "init")
        _send(iv, "setImage:", [c_void_p], [icon_img])
        _send(iv, "setFrame:", [_NSRect],
              [_rect((SPLASH_WIDTH - 96) / 2, 48, 96, 96)])
        _send(cv, "addSubview:", [c_void_p], [iv])
        _label(_TITLE, 26, 0.09, 0, 156, SPLASH_WIDTH, 34)
    else:
        _label(_TITLE, 34, 0.09, 0, 108, SPLASH_WIDTH, 44)
    _label(_SUBTITLE, 14, 0.45, 0, 196, SPLASH_WIDTH, 22)

    _send(win, "makeKeyAndOrderFront:", [c_void_p], [None])
    _send(win, "orderFrontRegardless")
    _send(nsapp, "activateIgnoringOtherApps:", [c_int], [1])

    # Pump the default run-loop mode for up to 1 s at a time; stop when the
    # parent process is gone (reparented to launchd) or the cap is reached.
    mode = cf.CFStringCreateWithCString(None, b"kCFRunLoopDefaultMode", 0)
    cf.CFRunLoopRunInMode.argtypes = [c_void_p, c_double, c_int]
    cf.CFRunLoopRunInMode.restype = c_int
    ppid = os.getppid()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        cf.CFRunLoopRunInMode(mode, 1.0, 1)
        if os.getppid() != ppid:
            break
        try:
            os.kill(ppid, 0)
        except ProcessLookupError:
            break
