"""OS-level cursor driver (xdotool) and CDP fallbacks."""
from __future__ import annotations

import asyncio
import math
import json
import os
import random
import subprocess
import time
from shutil import which

from nodriver import cdp

from rtk_cfg import OS_POINTER, TRAIL_SPEC, css_px, sample_band


class StepWallet:
    def __init__(self, max_moves: int):
        self.left = max_moves

    def take(self, n: int) -> int:
        if self.left <= 0:
            return 0
        n = max(1, min(n, self.left))
        self.left -= n
        return n


class GapClock:
    """Enforce a minimum wall-clock gap between CDP mouseMoved calls."""

    def __init__(self, min_dt_ms: float = 14.0):
        self.min_dt = max(0.008, float(min_dt_ms) / 1000.0)
        self._last = 0.0

    async def wait(self) -> None:
        now = time.monotonic()
        gap = self.min_dt - (now - self._last)
        if gap > 0:
            await asyncio.sleep(gap)

    def mark(self) -> None:
        self._last = time.monotonic()


class CssTrail:
    """Last integer CSS pixel — skip no-op moves."""

    __slots__ = ("x", "y")

    def __init__(self) -> None:
        self.x: int | None = None
        self.y: int | None = None

    @property
    def pos(self) -> tuple[float, float] | None:
        if self.x is None or self.y is None:
            return None
        return float(self.x), float(self.y)

    def advance(self, x: float, y: float) -> tuple[int, int] | None:
        xi, yi = css_px(x, y)
        if self.x == xi and self.y == yi:
            return None
        self.x, self.y = xi, yi
        return xi, yi

async def _set_input_mute(tab, muted: bool) -> None:
    """Gate pointer/mouse delivery to the collector (calibrate / park)."""
    try:
        await tab.evaluate(
            f"""(() => {{
              const slot = document[Symbol.for('rtk.mute')];
              if (slot) slot.on = {str(bool(muted)).lower()};
            }})()""",
            return_by_value=True,
        )
    except Exception:
        pass



class OsCursor:
    """Drive the real X cursor (xdotool) — OS input path, not CDP Input.*.

    Bot detectors fingerprint nodriver's Input.dispatchMouseEvent cadence
    (paired same-t pointer/mouse floods). XTest/xdotool goes through the
    platform widget path humans use under Xvfb.
    """

    def __init__(self) -> None:
        self._ox = 0
        self._oy = 0
        self._dpr = 1.0
        self._ready = False

    @property
    def ok(self) -> bool:
        return bool(which("xdotool") and os.environ.get("DISPLAY"))

    def _run(self, *args: str, check: bool = False) -> bool:
        try:
            proc = subprocess.run(
                ["xdotool", *args],
                check=check,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1.5,
                env=os.environ.copy(),
            )
            return proc.returncode == 0
        except Exception:
            return False

    def focus_browser(self) -> None:
        if not self.ok:
            return
        for cls in ("chromium", "Chromium", "google-chrome", "Google-chrome", "Chrome"):
            try:
                out = subprocess.check_output(
                    ["xdotool", "search", "--onlyvisible", "--class", cls],
                    stderr=subprocess.DEVNULL,
                    timeout=2,
                    env=os.environ.copy(),
                )
                ids = out.decode().strip().split()
                if ids:
                    self._run("windowactivate", "--sync", ids[0])
                    return
            except Exception:
                continue

    async def calibrate(self, tab) -> bool:
        if not self.ok:
            print(
                f"[bot] X11 not ok xdotool={which('xdotool')} DISPLAY={os.environ.get('DISPLAY')}",
                flush=True,
            )
            return False
        try:
            raw = await tab.evaluate(
                """[
                  window.screenX|0,
                  window.screenY|0,
                  window.outerWidth|0,
                  window.outerHeight|0,
                  window.innerWidth|0,
                  window.innerHeight|0,
                  window.devicePixelRatio||1
                ]""",
                return_by_value=True,
            )
            if hasattr(raw, "value") and raw.value is not None:
                raw = raw.value
            if not (isinstance(raw, (list, tuple)) and len(raw) >= 7):
                js = await tab.evaluate(
                    """JSON.stringify([
                      window.screenX|0, window.screenY|0,
                      window.outerWidth|0, window.outerHeight|0,
                      window.innerWidth|0, window.innerHeight|0,
                      window.devicePixelRatio||1
                    ])""",
                    return_by_value=True,
                )
                if isinstance(js, str):
                    raw = json.loads(js)
                elif hasattr(js, "value") and isinstance(js.value, str):
                    raw = json.loads(js.value)
            if not (isinstance(raw, (list, tuple)) and len(raw) >= 7):
                print(f"[bot] X11 calibrate bad metrics type={type(raw)} val={raw!r}", flush=True)
                return False
            sx, sy, ow, oh, iw, ih, dpr = (float(raw[i]) for i in range(7))
            top = max(0, int(oh - ih))
            side = max(0, int(ow - iw) // 2)
            dpr = float(dpr or 1.0)
            self._ox = int(round((sx + side) * dpr))
            self._oy = int(round((sy + top) * dpr))
            self._dpr = dpr
            self._iw = iw
            self._ih = ih
            self.focus_browser()
            try:
                for cx, cy in ((120, 120), (220, 180), (80, 80)):
                    rx = int(round(self._ox + cx * dpr))
                    ry = int(round(self._oy + cy * dpr))
                    self._run("mousemove", str(rx), str(ry))
                    await asyncio.sleep(0.05)
                    got = await tab.evaluate(
                        "document[Symbol.for('rtk.fit')] && document[Symbol.for('rtk.fit')].xy",
                        return_by_value=True,
                    )
                    if hasattr(got, "value"):
                        got = got.value
                    if isinstance(got, (list, tuple)) and len(got) >= 2:
                        gx, gy = float(got[0]), float(got[1])
                        self._ox = int(round(rx - gx * dpr))
                        self._oy = int(round(ry - gy * dpr))
                        print(
                            f"[bot] X11 active-fit target=({cx},{cy}) got=({gx:.1f},{gy:.1f}) "
                            f"-> ox={self._ox} oy={self._oy}",
                            flush=True,
                        )
                        break
            except Exception as exc:
                print(f"[bot] X11 active-fit skipped: {exc}", flush=True)
            self._ready = True
            probe = self._run("mousemove", str(self._ox + 20), str(self._oy + 20))
            print(
                f"[bot] X11 pointer ready ox={self._ox} oy={self._oy} dpr={dpr} "
                f"chrome_top={top} side={side} probe={probe}",
                flush=True,
            )
            if not probe:
                self._ready = False
                return False
            return True
        except Exception as exc:
            print(f"[bot] X11 calibrate failed: {exc}", flush=True)
            self._ready = False
            return False

    def to_root(self, css_x: float, css_y: float) -> tuple[int, int]:
        dpr = getattr(self, "_dpr", 1.0)
        return (
            int(round(self._ox + float(css_x) * dpr)),
            int(round(self._oy + float(css_y) * dpr)),
        )

    def move(self, css_x: float, css_y: float) -> bool:
        if not (self.ok and self._ready):
            return False
        rx, ry = self.to_root(css_x, css_y)
        try:
            geo = subprocess.check_output(
                ["xdotool", "getdisplaygeometry"],
                stderr=subprocess.DEVNULL,
                timeout=1,
                env=os.environ.copy(),
                text=True,
            ).strip()
            sw, sh = (int(x) for x in geo.split()[:2])
            rx = max(0, min(sw - 1, rx))
            ry = max(0, min(sh - 1, ry))
        except Exception:
            rx = max(0, rx)
            ry = max(0, ry)
        return self._run("mousemove", str(rx), str(ry))

    def click(self, css_x: float, css_y: float, dwell_ms: float) -> bool:
        if not (self.ok and self._ready):
            return False
        if not self.move(css_x, css_y):
            return False
        if not self._run("mousedown", "1"):
            return False
        time.sleep(max(0.05, dwell_ms / 1000.0))
        return self._run("mouseup", "1")

    def wheel(self, css_x: float, css_y: float, dy: float) -> bool:
        """One OS wheel notch (button 4/5). Caller owns burst timing."""
        if not (self.ok and self._ready):
            return False
        if not self.move(css_x, css_y):
            return False
        btn = "5" if dy > 0 else "4"
        return self._run("click", "--clearmodifiers", btn)


_CURSOR = OsCursor()


async def _moved(
    tab,
    x: float,
    y: float,
    pacer: GapClock | None = None,
    cursor: CssTrail | None = None,
    max_step_px: float | None = 32.0,
) -> tuple[int, int] | None:
    """One human-like move via X11; CDP mouseMoved only if not OS_POINTER.

    Large hops are split so the collector never sees velocity teleports.
    """
    xi, yi = css_px(x, y)
    if cursor is not None and cursor.pos is not None and max_step_px and max_step_px > 0:
        lx, ly = cursor.pos
        dx, dy = float(xi - lx), float(yi - ly)
        dist = math.hypot(dx, dy)
        limit = float(max_step_px)
        if dist > limit:
            n = max(2, int(math.ceil(dist / limit)))
            last = None
            for i in range(1, n + 1):
                t = i / n
                px = lx + dx * t
                py = ly + dy * t
                last = await _moved(
                    tab, px, py, pacer, cursor, max_step_px=None
                )
                if i < n:
                    await asyncio.sleep(random.uniform(0.008, 0.018))
            return last
    if cursor is not None:
        snapped = cursor.advance(xi, yi)
        if snapped is None:
            return None
        xi, yi = snapped
    if pacer is not None:
        await pacer.wait()
    if _CURSOR.ok and _CURSOR._ready:
        if not _CURSOR.move(float(xi), float(yi)):
            await asyncio.sleep(0.008)
            if not _CURSOR.move(float(xi), float(yi)):
                if pacer is not None:
                    pacer.mark()
                return None
    elif OS_POINTER:
        if pacer is not None:
            pacer.mark()
        return None
    else:
        await tab.send(
            cdp.input_.dispatch_mouse_event("mouseMoved", x=float(xi), y=float(yi))
        )
    if pacer is not None:
        pacer.mark()
    return xi, yi



async def _click_at(tab, x: float, y: float, profile: dict, *, allow_cdp: bool = True) -> bool:
    """Press/release at CSS pixel. CDP-dark never uses Input.dispatchMouseEvent."""
    xi, yi = css_px(x, y)
    dwell = sample_band(profile["click"]["down_up_ms"], lo=50.0, hi=220.0)
    allow_cdp = bool(allow_cdp) and not OS_POINTER
    if _CURSOR.ok and _CURSOR._ready:
        if _CURSOR.click(float(xi), float(yi), dwell):
            return True
        await asyncio.sleep(0.05)
        if _CURSOR.click(float(xi), float(yi), dwell):
            return True
        print("[bot] X11 click failed", flush=True)
        if not allow_cdp:
            return False
        print("[bot] CDP press/release last resort", flush=True)
    elif not allow_cdp:
        return False
    await tab.send(
        cdp.input_.dispatch_mouse_event(
            "mousePressed",
            x=float(xi),
            y=float(yi),
            button=cdp.input_.MouseButton("left"),
            buttons=1,
            click_count=1,
        )
    )
    await asyncio.sleep(dwell / 1000.0)
    await tab.send(
        cdp.input_.dispatch_mouse_event(
            "mouseReleased",
            x=float(xi),
            y=float(yi),
            button=cdp.input_.MouseButton("left"),
            buttons=0,
            click_count=1,
        )
    )
    return True



async def _x11_refit_to_css(tab, css_x: float, css_y: float) -> None:
    """Nudge X11 origin using live client coords from the NewDocument fit slot."""
    if not (_CURSOR.ok and _CURSOR._ready):
        return
    iw = float(getattr(_CURSOR, "_iw", 0.0) or 0.0)
    ih = float(getattr(_CURSOR, "_ih", 0.0) or 0.0)
    if iw > 0 and ih > 0:
        if not (8.0 <= float(css_x) <= iw - 8.0 and 8.0 <= float(css_y) <= ih - 8.0):
            return
    try:
        got = await tab.evaluate(
            "document[Symbol.for('rtk.fit')] && document[Symbol.for('rtk.fit')].xy",
            return_by_value=True,
        )
        if hasattr(got, "value"):
            got = got.value
        if not (isinstance(got, (list, tuple)) and len(got) >= 2):
            return
        gx, gy = float(got[0]), float(got[1])
        if iw > 0 and ih > 0 and not (
            -20.0 <= gx <= iw + 20.0 and -20.0 <= gy <= ih + 20.0
        ):
            return
        dpr = float(getattr(_CURSOR, "_dpr", 1.0) or 1.0)
        root_x = _CURSOR._ox + gx * dpr
        root_y = _CURSOR._oy + gy * dpr
        _CURSOR._ox = int(round(root_x - float(css_x) * dpr))
        _CURSOR._oy = int(round(root_y - float(css_y) * dpr))
    except Exception:
        pass



