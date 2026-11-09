"""Tests for the pre-Qt splash child (ca_pa.bootstrap) and the launcher."""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from ca_pa import bootstrap


def test_no_splash_env_disables_child(monkeypatch):
    monkeypatch.setenv("CA_PA_NO_SPLASH", "1")
    assert bootstrap.spawn_splash() is None


def test_linux_gets_no_native_child(monkeypatch):
    monkeypatch.delenv("CA_PA_NO_SPLASH", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    assert bootstrap.spawn_splash() is None
    assert bootstrap.is_alive(None) is False
    bootstrap.terminate_splash(None)  # must never raise


def test_terminate_and_is_alive_on_dead_proc():
    proc = subprocess.Popen(["true"])
    proc.wait()
    assert bootstrap.is_alive(proc) is False
    bootstrap.terminate_splash(proc)  # idempotent, must not raise


@pytest.mark.skipif(
    sys.platform not in ("win32", "darwin"),
    reason="a native splash child only exists on Windows/macOS",
)
def test_spawn_then_terminate_child():
    proc = bootstrap.spawn_splash()
    if proc is None:
        pytest.skip("splash child could not start in this environment")
    time.sleep(1.0)  # give it a moment to draw
    # Either it is up (great) or it already exited cleanly (the documented
    # silent-failure path, e.g. a headless CI session).  Both are fine; a
    # crash with a nonzero code is not.
    if bootstrap.is_alive(proc):
        bootstrap.terminate_splash(proc)
    assert proc.poll() is not None  # killed, or exited on its own
    bootstrap.terminate_splash(proc)  # idempotent


def test_child_branch_exits_cleanly():
    """The --splash-child entry point runs and exits 0 on any platform."""
    env = {**os.environ, "CA_PA_SPLASH_TIMEOUT": "0.5",
           "QT_QPA_PLATFORM": "offscreen"}
    env.pop("CA_PA_NO_SPLASH", None)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run(
        [sys.executable, "-m", "ca_pa.run_app", "--splash-child"],
        env=env, cwd=root, capture_output=True, timeout=90,
    )
    assert r.returncode == 0, r.stderr.decode(errors="replace")[-300:]


def test_child_timeout_env_is_respected():
    os.environ["CA_PA_SPLASH_TIMEOUT"] = "bogus"
    try:
        assert bootstrap._child_timeout() == bootstrap.CHILD_TIMEOUT_SECONDS
    finally:
        del os.environ["CA_PA_SPLASH_TIMEOUT"]
    os.environ["CA_PA_SPLASH_TIMEOUT"] = "0.25"
    try:
        assert bootstrap._child_timeout() == 0.25
    finally:
        del os.environ["CA_PA_SPLASH_TIMEOUT"]


def test_resources_dir_is_found():
    d = bootstrap._find_resources()
    assert os.path.exists(os.path.join(d, "ca-pa_icon.png"))
