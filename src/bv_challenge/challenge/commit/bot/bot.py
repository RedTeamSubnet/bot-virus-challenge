"""Bot Virus candidate — WebKitGTK read over the window-title channel.

A control model distinct from an eval/pull embedder (candidate 010), a
``ScriptMessageHandler`` push (candidate 015), a pixel-vision reader (candidate 012),
and a Firefox WebExtension bridge (candidate 009):

* The page renders in an embedded WebKitGTK ``WebView`` under Xvfb — no WebDriver, no
  CDP, no Playwright.
* The embedder NEVER evaluates JavaScript. A document-start user-script encodes the
  verify control's viewport rect, the real ``window.BV_SUBMITTED`` flag and the status
  text into ``document.title``; the host reads it back purely through the WebView's
  native ``get_title()`` property (a GObject property, not a query into the page).
* The click is a real ``isTrusted`` X pointer event delivered with XTEST at the
  button's screen position (webview screen origin + reported viewport rect).

Self-signed HTTPS is accepted with ``WebContext.set_tls_errors_policy(IGNORE)`` — a
browser-config call, not a socket. This script opens no network connections of its own.
"""

from __future__ import annotations

import contextlib
import logging
import math
import os
import subprocess
import time
from random import SystemRandom

from Xlib import X, display
from Xlib.ext import xtest

logger = logging.getLogger(__name__)

_SCREEN = (1600, 1000)
_XVFB: subprocess.Popen | None = None
_SESSION = None
_RNG = SystemRandom()

_DONE_TEXT = ("verification complete", "verified", "not a bot", "success")
_FAIL_TEXT = ("bot detected", "went wrong", "error", "failed")

# Xvfb is started and the GObject/WebKit stack bound at import: PyGObject snapshots
# GTK's initialised state at the gi.repository import, so it must run with Xvfb already
# up. Kept in a module-level guard (not a function) so the imports stay top-level.
Gtk = WebKit2 = None
try:
    if not os.environ.get("DISPLAY") and os.path.isfile("/usr/bin/Xvfb"):
        _XVFB = subprocess.Popen(
            ["/usr/bin/Xvfb", ":99", "-screen", "0", "1600x1000x24", "-nolisten", "tcp"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        os.environ["DISPLAY"] = ":99"
        for _ in range(40):
            time.sleep(0.25)
            _probe = None
            with contextlib.suppress(Exception):
                _probe = display.Display()
            if _probe is not None:
                _probe.close()
                break
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("WebKit2", "4.1")
    from gi.repository import Gtk, WebKit2
except Exception as exc:
    logger.warning("gtk/webkit bootstrap failed: %s", exc)

# document-start user-script. WebKitGTK user scripts run in the page's main world,
# so this runs directly: it encodes page state into document.title on a timer.
_REPORTER = r"""
(function () {
  function encode() {
    var el = document.getElementById('verify-button')
      || document.querySelector('.verify-button')
      || document.querySelector('button, [role=button]');
    var coord = 'na';
    if (el) {
      var r = el.getBoundingClientRect();
      if (r.width > 1 && r.height > 1) {
        coord = Math.round(r.left + r.width / 2) + ',' + Math.round(r.top + r.height / 2);
      }
    }
    var sub = (window.BV_SUBMITTED === true) ? 1 : 0;
    var status = '';
    var s = document.getElementById('status');
    if (s && s.textContent) { status = s.textContent.slice(0, 30).replace(/[|]/g, ' '); }
    document.title = 'BVB|' + coord + '|' + sub + '|' + status;
  }
  try { setInterval(encode, 250); } catch (e) { void e; }
  encode();
})();
"""


def _turn(seconds: float) -> None:
    """Advance the GTK/GLib loop so the webview renders, the title updates, and
    XTEST-injected pointer events are delivered."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        time.sleep(0.005)


class _Session:
    def __init__(self) -> None:
        self._xdisplay = display.Display()
        self._pointer = (_RNG.randint(140, 340), _RNG.randint(760, 980))

        os.environ["WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS"] = "1"
        os.environ.setdefault("WEBKIT_DISABLE_DMABUF_RENDERER", "1")
        os.environ.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")
        os.environ.setdefault("NO_AT_BRIDGE", "1")
        os.environ.setdefault("GTK_A11Y", "none")
        for _ in range(40):
            ready = Gtk.init_check()
            if ready and ready[0]:
                break
            time.sleep(0.25)

        manager = WebKit2.UserContentManager()
        manager.add_script(
            WebKit2.UserScript.new(
                _REPORTER,
                WebKit2.UserContentInjectedFrames.TOP_FRAME,
                WebKit2.UserScriptInjectionTime.START,
                None,
                None,
            )
        )
        context = WebKit2.WebContext()
        with contextlib.suppress(Exception):
            context.set_sandbox_enabled(False)
        with contextlib.suppress(Exception):
            context.set_tls_errors_policy(WebKit2.TLSErrorsPolicy.IGNORE)
        self._view = WebKit2.WebView(web_context=context, user_content_manager=manager)
        with contextlib.suppress(Exception):
            self._view.get_network_session().set_tls_errors_policy(WebKit2.TLSErrorsPolicy.IGNORE)

        self._window = Gtk.Window()
        self._window.set_decorated(False)
        self._window.move(0, 0)
        self._window.set_default_size(*_SCREEN)
        self._window.add(self._view)
        self._window.show_all()
        _turn(0.5)

    def load(self, url: str) -> None:
        self._view.load_uri(url)

    # -- read the native title property (the whole read path) --------------- #
    def read(self):
        title = ""
        with contextlib.suppress(Exception):
            title = self._view.get_title() or ""
        if "BVB|" not in title:
            return None, False, ""
        body = title[title.find("BVB|") + 4 :]
        fields = body.split("|")
        if len(fields) < 3:
            return None, False, ""
        coord = None
        if fields[0] != "na" and "," in fields[0]:
            try:
                vx, vy = fields[0].split(",", 1)
                coord = self._to_screen(int(vx), int(vy))
            except ValueError:
                coord = None
        return coord, fields[1] == "1", fields[2].strip().lower()

    def _to_screen(self, vx: int, vy: int) -> tuple[int, int]:
        gdk_window = self._view.get_window()
        ox = oy = 0
        if gdk_window is not None:
            with contextlib.suppress(Exception):
                origin = gdk_window.get_origin()
                ox, oy = origin[-2], origin[-1]
        return ox + vx, oy + vy

    # -- trusted XTEST pointer --------------------------------------------- #
    def _slide(self, x: int, y: int) -> None:
        xtest.fake_input(self._xdisplay, X.MotionNotify, x=int(x), y=int(y))
        self._xdisplay.sync()
        self._pointer = (int(x), int(y))

    def _dwell(self) -> float:
        if _RNG.random() < 0.05:
            return _RNG.uniform(0.12, 0.8)
        return max(0.006, _RNG.gauss(0.018, 0.004))

    def _reach(self, target: tuple[int, int], aim_sigma: float, jitter: float) -> None:
        """Momentum reach: the cursor carries a velocity pulled toward an aim point (with
        endpoint variance) each tick and perturbed by low-frequency correlated
        Ornstein-Uhlenbeck noise, so the path wanders organically before it converges --
        distinct from a min-jerk sub-movement, a spring, or a Bezier spline."""
        ax = target[0] + _RNG.gauss(0, aim_sigma)
        ay = target[1] + _RNG.gauss(0, aim_sigma)
        x, y = float(self._pointer[0]), float(self._pointer[1])
        vx = vy = nx = ny = 0.0
        pull = _RNG.uniform(0.016, 0.024)
        theta = 0.30
        sigma = jitter * 2.0
        for _ in range(260):
            nx += -theta * nx + _RNG.gauss(0, sigma)
            ny += -theta * ny + _RNG.gauss(0, sigma)
            vx = vx * 0.6 + (ax - x) * pull + nx * 0.12
            vy = vy * 0.6 + (ay - y) * pull + ny * 0.12
            x += vx
            y += vy
            self._slide(round(x), round(y))
            _turn(self._dwell())
            if abs(ax - x) < 1.5 and abs(ay - y) < 1.5 and math.hypot(vx, vy) < 0.6:
                break

    def reach_and_press(self, target: tuple[int, int]) -> None:
        self._reach((_RNG.randint(240, 1280), _RNG.randint(640, 980)), 24.0, 1.2)
        _turn(_RNG.uniform(0.6, 1.3))
        for _ in range(_RNG.randint(2, 5)):
            self._slide(
                self._pointer[0] + _RNG.randint(-7, 7), self._pointer[1] + _RNG.randint(-5, 5)
            )
            _turn(_RNG.uniform(0.10, 0.42))
        tx, ty = target
        self._reach((tx, ty), 20.0, 1.0)
        for _ in range(_RNG.randint(2, 4)):
            self._slide(tx + _RNG.randint(-1, 1), ty + _RNG.randint(-1, 1))
            _turn(self._dwell())
        _turn(_RNG.uniform(0.15, 0.4))
        xtest.fake_input(self._xdisplay, X.ButtonPress, 1)
        self._xdisplay.sync()
        _turn(_RNG.uniform(0.05, 0.09))
        self._slide(self._pointer[0] + _RNG.randint(0, 2), self._pointer[1] + _RNG.randint(0, 1))
        _turn(_RNG.uniform(0.02, 0.05))
        xtest.fake_input(self._xdisplay, X.ButtonRelease, 1)
        self._xdisplay.sync()

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._window.destroy()
        _turn(0.1)


def _await_target(session: _Session, deadline: float):
    while time.monotonic() < deadline:
        coord, _submitted, _status = session.read()
        if coord is not None:
            return coord
        _turn(0.3)
    return None


def _await_finish(session: _Session, deadline: float) -> bool:
    while time.monotonic() < deadline:
        _coord, submitted, status = session.read()
        if submitted or any(term in status for term in _DONE_TEXT):
            return True
        if any(term in status for term in _FAIL_TEXT):
            return False
        _turn(0.4)
    return False


def _run_session(url: str) -> bool:
    global _SESSION
    if Gtk is None:
        return False
    if _SESSION is not None:
        _SESSION.close()
    _SESSION = _Session()
    _SESSION.load(url)

    target = _await_target(_SESSION, time.monotonic() + 25.0)
    if target is None:
        logger.warning("verify control never titled")
        return False

    time.sleep(_RNG.uniform(0.8, 1.6))
    _SESSION.reach_and_press(target)

    return _await_finish(_SESSION, time.monotonic() + 30.0)


def run_bot(url: str) -> bool:
    """Complete one verification session; return True only on confirmed submit."""
    if not url:
        return False
    result = False
    with contextlib.suppress(Exception):
        result = _run_session(url)
    return result