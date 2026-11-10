"""WebKitGTK runner for the Bot Virus browser check."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import random
import string
from pathlib import Path
import subprocess
import time
from typing import Any

logger = logging.getLogger(__name__)

_DISPLAY = ":99"
_SCREEN = (1440, 900)
_XVFB_BIN = "/usr/bin/Xvfb"
_CHANNEL = "bvstate"

_TARGET_NAMES = (
    "click-here-1",
    "click-here-2",
    "click-here-3",
    "username",
    "password",
    "submit",
)
_MIN_INPUT_LEN = 16

# Runs before any page script. It keeps the only reference to the native
# message port inside this closure -- nothing is published on `window` -- and
# reports target geometries plus a submission flag as they change.
_PAGE_AGENT = """
(() => {
  let post;
  try {
    const port = window.webkit.messageHandlers.%(channel)s;
    post = port.postMessage.bind(port);
  } catch (_) {
    return;
  }

  try {
    const notification = window.Notification;
    if (notification && notification.permission === "default") {
      Object.defineProperty(notification, "permission", {
        configurable: true,
        enumerable: true,
        get: () => "prompt",
      });
    }
  } catch (_) {}

  let settled = false;
  const settle = () => {
    if (settled) { return; }
    settled = true;
    post(JSON.stringify({submitted: true}));
  };

  try {
    let flag = false;
    Object.defineProperty(window, "BV_SUBMITTED", {
      configurable: true,
      get: () => flag,
      set: (value) => {
        flag = value === true;
        if (flag) { settle(); }
      },
    });
  } catch (_) {}

  const TARGETS = %(targets)s;
  const lastKey = {};

  const measureOne = (name) => {
    const el = document.querySelector("#" + name);
    if (!el) { return; }
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) { return; }
    const key = r.left + ":" + r.top + ":" + r.width + ":" + r.height
      + ":" + (el.disabled ? "d" : "e");
    if (key === lastKey[name]) { return; }
    lastKey[name] = key;
    post(JSON.stringify({
      target: {
        name: name,
        x: r.left + r.width / 2,
        y: r.top + r.height / 2,
        w: r.width,
        h: r.height,
        disabled: !!el.disabled,
      },
    }));
  };

  const measure = () => {
    if (window.BV_SUBMITTED === true) { settle(); }
    for (const name of TARGETS) { measureOne(name); }
    const status = document.querySelector(".status");
    if (status && /submit/i.test(status.textContent || "")) { settle(); }
  };

  const watch = () => {
    try {
      new MutationObserver(measure).observe(document.documentElement, {
        childList: true,
        subtree: true,
        attributes: true,
      });
    } catch (_) {}
    measure();
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", watch, {once: true});
  } else {
    watch();
  }
  window.addEventListener("load", measure);
})();
"""


def _start_display() -> None:
    if os.environ.get("DISPLAY"):
        return
    if not Path(_XVFB_BIN).exists():
        raise RuntimeError("Xvfb is not available")
    subprocess.Popen(
        ["/usr/bin/Xvfb", ":99", "-screen", "0", "1440x900x24"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    os.environ["DISPLAY"] = _DISPLAY
    time.sleep(1.2)


os.environ.setdefault("WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS", "1")
os.environ.setdefault("WEBKIT_DISABLE_DMABUF_RENDERER", "1")
os.environ.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")
os.environ.setdefault("NO_AT_BRIDGE", "1")
os.environ.setdefault("GTK_A11Y", "none")

Gdk = Gtk = WebKit2 = None
try:
    _start_display()

    import gi

    gi.require_version("Gdk", "3.0")
    gi.require_version("Gtk", "3.0")
    gi.require_version("WebKit2", "4.1")
    from gi.repository import Gdk, Gtk, WebKit2
except (ImportError, OSError, RuntimeError, ValueError) as exc:
    logger.warning("browser bootstrap failed: %s", exc)


def _pump(seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while Gtk and Gtk.events_pending():
            Gtk.main_iteration_do(False)
        time.sleep(0.01)


def _parse_json(value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return None
    with contextlib.suppress(json.JSONDecodeError):
        return json.loads(value)
    return None


class _Pointer:
    """Feeds pointer events straight into the toolkit's own event queue."""

    def __init__(self, view) -> None:
        self._view = view
        seat = Gdk.Display.get_default().get_default_seat()
        self._device = seat.get_pointer() if seat else None
        if self._device is None:
            raise RuntimeError("no pointer device on the default seat")
        self._at = (float(_SCREEN[0] // 2), float(_SCREEN[1] - 80))
        self._held = False

    @property
    def at(self) -> tuple[float, float]:
        return self._at

    def _emit(self, kind, x: float, y: float, button: int | None = None) -> bool:
        surface = self._view.get_window()
        if surface is None:
            return False
        event = Gdk.Event.new(kind)
        event.window = surface
        event.send_event = 0
        event.time = int(time.monotonic() * 1000) & 0xFFFFFFFF
        event.x, event.y = x, y
        event.x_root, event.y_root = x, y
        event.state = Gdk.ModifierType.BUTTON1_MASK if self._held else 0
        if button is not None:
            event.button = button
        if kind == Gdk.EventType.MOTION_NOTIFY:
            event.is_hint = 0
        event.set_device(self._device)
        event.set_source_device(self._device)
        Gtk.main_do_event(event)
        return True

    def move(self, x: float, y: float) -> bool:
        safe_x = max(1.0, min(float(_SCREEN[0] - 2), x))
        safe_y = max(1.0, min(float(_SCREEN[1] - 2), y))
        if not self._emit(Gdk.EventType.MOTION_NOTIFY, safe_x, safe_y):
            return False
        self._at = (safe_x, safe_y)
        return True

    def press(self) -> bool:
        x, y = self._at
        ok = self._emit(Gdk.EventType.BUTTON_PRESS, x, y, button=1)
        self._held = ok
        return ok

    def release(self) -> bool:
        x, y = self._at
        self._held = False
        return self._emit(Gdk.EventType.BUTTON_RELEASE, x, y, button=1)


class _Keyboard:
    """Delivers key events through the same path a physical keyboard would."""

    def __init__(self, view) -> None:
        self._view = view
        seat = Gdk.Display.get_default().get_default_seat()
        self._device = seat.get_keyboard() if seat else None
        if self._device is None:
            raise RuntimeError("no keyboard device on the default seat")

    def _emit(self, kind, keyval: int, state: int = 0) -> bool:
        surface = self._view.get_window()
        if surface is None:
            return False
        event = Gdk.Event.new(kind)
        event.window = surface
        event.send_event = 0
        event.time = int(time.monotonic() * 1000) & 0xFFFFFFFF
        event.keyval = keyval
        event.hardware_keycode = 0
        event.group = 0
        event.state = state
        event.set_device(self._device)
        event.set_source_device(self._device)
        Gtk.main_do_event(event)
        return True

    def tap(self, char: str) -> bool:
        if not char:
            return False
        state = 0
        if char.isupper():
            state = Gdk.ModifierType.SHIFT_MASK
            keyval = ord(char.lower())
        else:
            keyval = ord(char)
        if not self._emit(Gdk.EventType.KEY_PRESS, keyval, state):
            return False
        _pump(0.01 + random.uniform(0.0, 0.03))
        return self._emit(Gdk.EventType.KEY_RELEASE, keyval, state)


class _Browser:
    def __init__(self) -> None:
        ready = Gtk.init_check()
        if not ready or not ready[0]:
            raise RuntimeError("GTK could not connect to the display")

        self._load_done = False
        self._load_failed = False
        self.targets: dict[str, dict[str, Any]] = {}
        self.submitted = False

        context = WebKit2.WebContext()
        with contextlib.suppress(Exception):
            context.set_tls_errors_policy(WebKit2.TLSErrorsPolicy.IGNORE)

        content_manager = WebKit2.UserContentManager()
        content_manager.register_script_message_handler(_CHANNEL)
        content_manager.connect(
            f"script-message-received::{_CHANNEL}",
            self._on_page_message,
        )
        script = _PAGE_AGENT % {
            "channel": _CHANNEL,
            "targets": json.dumps(list(_TARGET_NAMES)),
        }
        content_manager.add_script(
            WebKit2.UserScript(
                script,
                WebKit2.UserContentInjectedFrames.ALL_FRAMES,
                WebKit2.UserScriptInjectionTime.START,
                None,
                None,
            )
        )
        try:
            self._view = WebKit2.WebView(
                web_context=context,
                user_content_manager=content_manager,
            )
        except TypeError:
            self._view = WebKit2.WebView.new_with_user_content_manager(content_manager)

        self._view.connect("load-changed", self._on_load_changed)
        self._view.connect("load-failed", self._on_load_failed)

        self._window = Gtk.Window()
        self._window.set_decorated(False)
        self._window.move(0, 0)
        self._window.set_default_size(*_SCREEN)
        self._window.add(self._view)
        self._window.show_all()
        _pump(0.4)

        self.pointer = _Pointer(self._view)
        self.keyboard = _Keyboard(self._view)

    @property
    def view(self):
        return self._view

    def _on_page_message(self, _manager, result) -> None:
        try:
            report = _parse_json(result.get_js_value().to_string())
        except (AttributeError, RuntimeError, TypeError) as exc:
            logger.debug("page report unreadable: %s", exc)
            return
        if not isinstance(report, dict):
            return
        if report.get("submitted") is True:
            self.submitted = True
        spot = report.get("target")
        if isinstance(spot, dict) and {"name", "x", "y"} <= spot.keys():
            self.targets[str(spot["name"])] = spot

    def _on_load_changed(self, _view, event) -> None:
        if event == WebKit2.LoadEvent.FINISHED:
            self._load_done = True

    def _on_load_failed(self, _view, _event, _uri, _error) -> bool:
        self._load_failed = True
        return False

    def open(self, url: str) -> None:
        self._load_done = False
        self._load_failed = False
        self._view.load_uri(url)

    def wait_loaded(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not self._load_done:
            if self._load_failed:
                return False
            _pump(0.05)
        return self._load_done and not self._load_failed

    def await_target(self, name: str, timeout: float) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            spot = self.targets.get(name)
            if spot is not None:
                return spot
            _pump(0.05)
        return None

    def await_enabled(self, name: str, timeout: float) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            spot = self.targets.get(name)
            if spot and not spot.get("disabled"):
                return spot
            _pump(0.05)
        return self.targets.get(name)

    def await_submission(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.submitted:
                return True
            _pump(0.05)
        return self.submitted

    def confirm_submission(self, timeout: float = 1.5) -> bool:
        box: dict[str, Any] = {"done": False, "value": None}

        def finished(view, result, _data=None) -> None:
            try:
                value = view.run_javascript_finish(result).get_js_value()
                if value and value.is_string():
                    box["value"] = value.to_string()
            except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
                logger.debug("confirmation read failed: %s", exc)
            finally:
                box["done"] = True

        self._view.run_javascript(
            "JSON.stringify({ok: window.BV_SUBMITTED === true "
            "|| /submit/i.test((document.querySelector('.status')||{}).textContent||'')"
            "})",
            None,
            finished,
            None,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not box["done"]:
            _pump(0.03)
        state = _parse_json(box["value"])
        return bool(isinstance(state, dict) and state.get("ok") is True)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._window.destroy()
        _pump(0.1)


def _reach(pointer: _Pointer, x: float, y: float) -> bool:
    origin = pointer.at
    steps = 10
    for step in range(1, steps + 1):
        part = step / steps
        moved = pointer.move(
            origin[0] + (x - origin[0]) * part,
            origin[1] + (y - origin[1]) * part,
        )
        if not moved:
            return False
        _pump(0.045)
    return True


def _click_at(pointer: _Pointer, x: float, y: float) -> bool:
    if not _reach(pointer, x, y):
        return False
    _pump(0.1)
    if not pointer.press():
        return False
    _pump(0.07)
    return pointer.release()


def _type_text(keyboard: _Keyboard, text: str) -> bool:
    for ch in text:
        if not keyboard.tap(ch):
            return False
        _pump(0.04 + random.uniform(0.0, 0.05))
    return True


def _random_text(length: int) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


def _click_target(browser: _Browser, name: str, timeout: float = 6.0) -> bool:
    spot = browser.await_target(name, timeout)
    if spot is None:
        logger.warning("target %s not found", name)
        return False
    return _click_at(browser.pointer, float(spot["x"]), float(spot["y"]))


def _fill_input(
    browser: _Browser, name: str, text: str, timeout: float = 6.0
) -> bool:
    spot = browser.await_target(name, timeout)
    if spot is None:
        logger.warning("input %s not found", name)
        return False
    if not _click_at(browser.pointer, float(spot["x"]), float(spot["y"])):
        return False
    _pump(0.15)
    return _type_text(browser.keyboard, text)


def _run_once(url: str) -> bool:
    if Gtk is None:
        return False

    browser = _Browser()
    try:
        browser.open(url)
        if not browser.wait_loaded(20.0):
            return False

        _pump(1.1)

        for corner in ("click-here-1", "click-here-2", "click-here-3"):
            if not _click_target(browser, corner, timeout=8.0):
                return browser.await_submission(5.0) or browser.confirm_submission()
            _pump(0.3)

        username = _random_text(_MIN_INPUT_LEN + random.randint(2, 6))
        password = _random_text(_MIN_INPUT_LEN + random.randint(2, 6))

        if not _fill_input(browser, "username", username):
            return browser.await_submission(5.0) or browser.confirm_submission()
        _pump(0.25)
        if not _fill_input(browser, "password", password):
            return browser.await_submission(5.0) or browser.confirm_submission()
        _pump(0.35)

        submit_spot = browser.await_enabled("submit", timeout=4.0)
        if submit_spot is None:
            return browser.await_submission(5.0) or browser.confirm_submission()

        for _ in range(3):
            if not _click_at(
                browser.pointer, float(submit_spot["x"]), float(submit_spot["y"])
            ):
                break
            if browser.await_submission(4.0):
                return True
            submit_spot = browser.targets.get("submit") or submit_spot

        return browser.await_submission(10.0) or browser.confirm_submission()
    finally:
        browser.close()


def run_bot(url: str) -> bool:
    """Run one browser verification session."""
    if not url:
        return False
    try:
        return _run_once(url)
    except (OSError, RuntimeError) as exc:
        logger.warning("browser run failed: %s", exc)
        return False
