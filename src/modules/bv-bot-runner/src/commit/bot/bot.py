"""Bot Virus miner: nodriver + AdSERP-fitted human pointer dynamics."""

from __future__ import annotations

import asyncio
import json
import math
import os
import random
import subprocess
import time
from shutil import which

import nodriver as uc
from nodriver import cdp

MOVE_DEDUP_MODE = (os.environ.get("BV_MOVE_DEDUP") or "suppress_mouse").strip().lower()
if MOVE_DEDUP_MODE not in ("off", "suppress_mouse", "suppress_pointer"):
    MOVE_DEDUP_MODE = "suppress_mouse"

CDP_DARK = (os.environ.get("BV_CDP_DARK") or "1").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

MOTION_PROFILE = {
    "version": 3,
    "source": ["adserp", "psai"],
    "path": {
        "dt_ms": {"p10": 9.0, "p50": 19.0, "p90": 48.0},
        "steps_per_100px": {"p10": 4.0, "p50": 7.0, "p90": 10.0},
        "ctrl_offset_frac": {"p10": 0.018, "p50": 0.084, "p90": 0.49},
        "noise_px": {"p10": 0.0, "p50": 0.4, "p90": 1.6},
        "ease": "piecewise",
        "min_move_dt_ms": 8.0,
        "hesitation_p": 0.10,
        "flick_p": 0.14,
        "max_step_px": 32.0,
        "overshoot_p": 0.42,
    },
    "session": {
        "wander_count": [0, 3],
        "inter_stroke_pause_ms": {"p10": 209.0, "p50": 427.0, "p90": 1303.0},
        "pre_click_hover_ms": {"p10": 80.0, "p50": 200.0, "p90": 360.0},
        "approach_slowdown": {"p10": 0.10, "p50": 0.38, "p90": 1.25},
        "max_moves_budget": 90,
        "micro_jitter_n": [1, 4],
    },
    "click": {"down_up_ms": {"p10": 69.0, "p50": 110.0, "p90": 182.0}},
    "scroll": {
        "tick_dy": [48, 64, 80, 96],
        "down_ticks": [2, 3],
        "up_ticks": [0, 1],
        "gap_ms": [55, 140],
        "burst_pause_ms": [160, 340],
    },
}


def _chrome_binary() -> str:
    for candidate in (
        which("google-chrome"),
        which("google-chrome-stable"),
        which("chromium"),
        which("chromium-browser"),
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
    ):
        if candidate:
            return candidate
    return "/usr/bin/chromium"


VIEWPORTS = [
    {"id": "fhd-1536", "screen_w": 1920, "screen_h": 1080, "taskbar": 40, "win_w": 1536, "win_h": 864},
    {"id": "fhd-1440", "screen_w": 1920, "screen_h": 1080, "taskbar": 48, "win_w": 1440, "win_h": 900},
    {"id": "fhd-1366", "screen_w": 1920, "screen_h": 1080, "taskbar": 40, "win_w": 1366, "win_h": 768},
    {"id": "hd-1280", "screen_w": 1366, "screen_h": 768, "taskbar": 40, "win_w": 1280, "win_h": 720},
    {"id": "wxga-1280", "screen_w": 1440, "screen_h": 900, "taskbar": 37, "win_w": 1280, "win_h": 800},
    {"id": "wsxga-1440", "screen_w": 1680, "screen_h": 1050, "taskbar": 40, "win_w": 1440, "win_h": 900},
    {"id": "qhd-1600", "screen_w": 2560, "screen_h": 1440, "taskbar": 42, "win_w": 1600, "win_h": 900},
    {"id": "fhd-1600", "screen_w": 1920, "screen_h": 1200, "taskbar": 40, "win_w": 1600, "win_h": 1000},
]


def _pick_viewport() -> dict:
    vp = dict(random.choice(VIEWPORTS))
    vp["win_w"] = int(vp["win_w"] + random.randint(-24, 24))
    vp["win_h"] = int(vp["win_h"] + random.randint(-16, 16))
    vp["win_w"] = max(1180, min(vp["win_w"], vp["screen_w"] - 40))
    vp["win_h"] = max(700, min(vp["win_h"], vp["screen_h"] - vp["taskbar"] - 20))
    vp["pos_x"] = random.randint(0, max(0, vp["screen_w"] - vp["win_w"] - 8))
    vp["pos_y"] = random.randint(0, max(0, min(48, vp["screen_h"] - vp["win_h"] - 8)))
    vp["avail_w"] = vp["screen_w"]
    vp["avail_h"] = vp["screen_h"] - int(vp["taskbar"])
    return vp


def _display_dimensions(display: str) -> tuple[int, int] | None:
    if not which("xdpyinfo"):
        return None
    try:
        out = subprocess.check_output(
            ["xdpyinfo", "-display", display],
            stderr=subprocess.DEVNULL,
            timeout=2,
            text=True,
        )
    except Exception:
        return None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("dimensions:"):
            # e.g. dimensions:    2560x1440 pixels
            try:
                token = line.split()[1]
                w_s, h_s = token.split("x")
                return int(w_s), int(h_s)
            except Exception:
                return None
    return None


def _ensure_display(min_w: int = 1920, min_h: int = 1200) -> None:
    """Ensure an Xvfb large enough for the chosen window (not a fixed 1440x900)."""
    display = os.environ.get("DISPLAY", ":99")
    os.environ["DISPLAY"] = display
    need_w = max(2560, int(min_w) + 80)
    need_h = max(1440, int(min_h) + 80)
    dims = _display_dimensions(display)
    if dims is not None and dims[0] >= min_w and dims[1] >= min_h:
        return
    if dims is not None:
        try:
            subprocess.run(
                ["pkill", "-f", f"Xvfb {display}"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
            time.sleep(0.4)
        except Exception:
            pass
    if which("Xvfb"):
        subprocess.Popen(
            [
                "Xvfb",
                display,
                "-screen",
                "0",
                f"{need_w}x{need_h}x24",
                "-ac",
                "+extension",
                "RANDR",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(1.0)


def _sample_band(band: dict, lo: float | None = None, hi: float | None = None) -> float:
    """Sample roughly between p10 and p90, biased toward p50."""
    p10 = float(band.get("p10", 0.0))
    p50 = float(band.get("p50", p10))
    p90 = float(band.get("p90", p50))
    if lo is not None:
        p10 = max(p10, lo)
        p50 = max(p50, lo)
        p90 = max(p90, lo)
    if hi is not None:
        p10 = min(p10, hi)
        p50 = min(p50, hi)
        p90 = min(p90, hi)
    if random.random() < 0.5:
        return random.uniform(p10, p50)
    return random.uniform(p50, p90)


def _move_dt_s(path_cfg: dict, pace: float = 1.0) -> float:
    """Irregular inter-move delay — avoid metronomic 16–28ms CDP texture."""
    r = random.random()
    flick_p = float(path_cfg.get("flick_p", 0.12))
    hes_p = float(path_cfg.get("hesitation_p", 0.10))
    if r < flick_p:
        ms = random.uniform(8.0, 14.0)
    elif r < flick_p + hes_p:
        ms = random.uniform(65.0, 170.0)
    else:
        # Lognormal centered ~20ms with fat upper tail.
        ms = random.lognormvariate(math.log(20.0), 0.38)
        ms = max(8.0, min(70.0, ms))
    return max(0.006, (ms * max(0.5, pace)) / 1000.0)


def _css_px(x: float, y: float) -> tuple[int, int]:
    """Round to integer CSS pixels — kills pointer vs mouse sub-pixel divergence."""
    return int(round(float(x))), int(round(float(y)))


def _ease(t: float, kind: str = "piecewise") -> float:
    t = min(1.0, max(0.0, t))
    if kind in ("piecewise", "smoothstep"):
        return t * t * (3.0 - 2.0 * t)
    if kind == "cosine":
        return 0.5 - 0.5 * math.cos(math.pi * t)
    return t


def _densify_steps(
    pts: list[tuple[int, int]], max_step: float
) -> list[tuple[int, int]]:
    """Insert intermediates so no hop exceeds max_step px."""
    if not pts:
        return pts
    max_step = max(8.0, float(max_step))
    out: list[tuple[int, int]] = [pts[0]]
    for x, y in pts[1:]:
        lx, ly = out[-1]
        dx, dy = float(x - lx), float(y - ly)
        dist = math.hypot(dx, dy)
        if dist <= max_step:
            if (x, y) != out[-1]:
                out.append((x, y))
            continue
        n = max(2, int(math.ceil(dist / max_step)))
        for i in range(1, n + 1):
            t = i / n
            jx = jy = 0.0
            if i < n and dist > max_step:
                jx = float(random.choice([-1, 0, 0, 1]))
                jy = float(random.choice([-1, 0, 0, 1]))
            xi, yi = _css_px(lx + dx * t + jx, ly + dy * t + jy)
            if out[-1] != (xi, yi):
                out.append((xi, yi))
        if out[-1] != (x, y):
            out.append((x, y))
    return out


def _piecewise_points(
    start: tuple[float, float],
    end: tuple[float, float],
    steps: int,
    lateral_frac: float,
    noise_px: float,
    overshoot_p: float = 0.42,
    max_step_px: float = 32.0,
) -> list[tuple[int, int]]:
    """Noisy polyline + optional overshoot — not a cubic Bezier (humanize tell)."""
    x0, y0 = float(start[0]), float(start[1])
    x1, y1 = float(end[0]), float(end[1])
    dx, dy = x1 - x0, y1 - y0
    dist = math.hypot(dx, dy) or 1.0
    ux, uy = dx / dist, dy / dist
    px, py = -uy, ux

    if dist < 120:
        n_mids = 1
    elif dist < 320:
        n_mids = 2
    else:
        n_mids = 3

    waypoints: list[tuple[float, float]] = [(x0, y0)]
    for k in range(1, n_mids + 1):
        t = k / (n_mids + 1) + random.uniform(-0.07, 0.07)
        t = max(0.12, min(0.88, t))
        t = _ease(t, "smoothstep")
        lat = (
            float(lateral_frac)
            * dist
            * random.choice([-1.0, 1.0])
            * random.uniform(0.25, 1.05)
        )
        along = random.uniform(-0.04, 0.04) * dist
        waypoints.append(
            (
                x0 + dx * t + px * lat + ux * along,
                y0 + dy * t + py * lat + uy * along,
            )
        )

    if dist > 70 and random.random() < max(0.0, min(1.0, float(overshoot_p))):
        over = random.uniform(5.0, 16.0)
        waypoints.append(
            (
                x1 + ux * over + px * random.uniform(-4.0, 4.0),
                y1 + uy * over + py * random.uniform(-4.0, 4.0),
            )
        )
    waypoints.append((x1, y1))

    seg_lens = [
        math.hypot(waypoints[i + 1][0] - waypoints[i][0], waypoints[i + 1][1] - waypoints[i][1])
        or 1.0
        for i in range(len(waypoints) - 1)
    ]
    total = sum(seg_lens)
    noise_mag = max(0, int(round(float(noise_px))))
    coarse: list[tuple[int, int]] = [_css_px(x0, y0)]
    steps_left = max(2, int(steps))
    for i, (slen) in enumerate(seg_lens):
        ax, ay = waypoints[i]
        bx, by = waypoints[i + 1]
        seg_steps = max(1, int(round(steps_left * (slen / total)))) if i < len(seg_lens) - 1 else steps_left
        steps_left = max(0, steps_left - seg_steps)
        for s in range(1, seg_steps + 1):
            te = _ease(s / seg_steps, "smoothstep")
            te = min(1.0, max(0.0, te + random.uniform(-0.04, 0.04)))
            x = ax + (bx - ax) * te
            y = ay + (by - ay) * te
            if noise_mag and s < seg_steps:
                x += float(random.randint(-noise_mag, noise_mag))
                y += float(random.randint(-noise_mag, noise_mag))
            xi, yi = _css_px(x, y)
            if coarse[-1] != (xi, yi):
                coarse.append((xi, yi))
    end_i = _css_px(x1, y1)
    if coarse[-1] != end_i:
        coarse.append(end_i)
    return _densify_steps(coarse, max_step_px)


async def _set_input_mute(tab, muted: bool) -> None:
    """Gate pointer/mouse delivery to the collector (calibrate / park)."""
    try:
        await tab.evaluate(
            f"""(() => {{
              const slot = document[Symbol.for('bv.input.mute')];
              if (slot) slot.on = {str(bool(muted)).lower()};
            }})()""",
            return_by_value=True,
        )
    except Exception:
        pass


class MoveBudget:
    def __init__(self, max_moves: int):
        self.left = max_moves

    def take(self, n: int) -> int:
        if self.left <= 0:
            return 0
        n = max(1, min(n, self.left))
        self.left -= n
        return n


class MovePacer:
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


class PixelCursor:
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
        xi, yi = _css_px(x, y)
        if self.x == xi and self.y == yi:
            return None
        self.x, self.y = xi, yi
        return xi, yi


class X11Pointer:
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
                        "document[Symbol.for('bv.mm')] && document[Symbol.for('bv.mm')].xy",
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


_X11 = X11Pointer()


async def _moved(
    tab,
    x: float,
    y: float,
    pacer: MovePacer | None = None,
    cursor: PixelCursor | None = None,
    max_step_px: float | None = 32.0,
) -> tuple[int, int] | None:
    """One human-like move via X11; CDP mouseMoved only if not CDP_DARK.

    Large hops are split so the collector never sees velocity teleports.
    """
    xi, yi = _css_px(x, y)
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
    if _X11.ok and _X11._ready:
        if not _X11.move(float(xi), float(yi)):
            await asyncio.sleep(0.008)
            if not _X11.move(float(xi), float(yi)):
                if pacer is not None:
                    pacer.mark()
                return None
    elif CDP_DARK:
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


async def _path(
    tab,
    start: tuple[float, float],
    end: tuple[float, float],
    profile: dict,
    budget: MoveBudget,
    pacer: MovePacer,
    cursor: PixelCursor,
    slowdown: float = 1.0,
) -> tuple[float, float]:
    path_cfg = profile["path"]
    start_i = _css_px(*start)
    end_i = _css_px(*end)
    dist = math.hypot(end_i[0] - start_i[0], end_i[1] - start_i[1])
    steps_per_100 = _sample_band(path_cfg["steps_per_100px"], lo=3.5, hi=10.0)
    raw_steps = max(6, int(dist / 100.0 * steps_per_100))
    steps = budget.take(raw_steps)
    if steps <= 0:
        await _moved(
            tab,
            end_i[0],
            end_i[1],
            pacer,
            cursor,
            max_step_px=float(path_cfg.get("max_step_px", 32.0)),
        )
        return float(end_i[0]), float(end_i[1])
    ctrl = _sample_band(path_cfg["ctrl_offset_frac"], lo=0.02, hi=0.35)
    noise = _sample_band(path_cfg["noise_px"], lo=0.0, hi=1.5)
    max_step = float(path_cfg.get("max_step_px", 32.0))
    overshoot_p = float(path_cfg.get("overshoot_p", 0.42))
    pts = _piecewise_points(
        start_i,
        end_i,
        steps,
        ctrl,
        noise,
        overshoot_p=overshoot_p,
        max_step_px=max_step,
    )
    extra = max(0, len(pts) - steps)
    if extra:
        allowed_extra = budget.take(extra)
        keep = steps + allowed_extra
        if len(pts) > keep and keep >= 2:
            sampled = [pts[0]]
            inner = pts[1:-1]
            need = keep - 2
            if need > 0 and inner:
                for j in range(need):
                    idx = int(round(j * (len(inner) - 1) / max(1, need - 1))) if need > 1 else 0
                    sampled.append(inner[min(idx, len(inner) - 1)])
            sampled.append(end_i)
            pts = sampled
    sent = 0
    path_max = float(path_cfg.get("max_step_px", 32.0))
    for i, (x, y) in enumerate(pts):
        out = await _moved(tab, x, y, pacer, cursor, max_step_px=path_max)
        if out is None:
            continue
        sent += 1
        frac = (i + 1) / max(1, len(pts))
        pace = 1.0 + (slowdown - 1.0) * frac
        await asyncio.sleep(_move_dt_s(path_cfg, pace=pace))
    if sent == 0 or cursor.pos != (float(end_i[0]), float(end_i[1])):
        cur = cursor.pos or (float(start_i[0]), float(start_i[1]))
        settle = _densify_steps(
            [_css_px(*cur), end_i],
            float(path_cfg.get("max_step_px", 32.0)),
        )
        for x, y in settle[1:]:
            await _moved(tab, x, y, pacer, cursor, max_step_px=path_max)
            await asyncio.sleep(_move_dt_s(path_cfg, pace=slowdown))
    return float(end_i[0]), float(end_i[1])


async def _click_at(tab, x: float, y: float, profile: dict, *, allow_cdp: bool = True) -> bool:
    """Press/release at CSS pixel. CDP-dark never uses Input.dispatchMouseEvent."""
    xi, yi = _css_px(x, y)
    dwell = _sample_band(profile["click"]["down_up_ms"], lo=50.0, hi=220.0)
    allow_cdp = bool(allow_cdp) and not CDP_DARK
    if _X11.ok and _X11._ready:
        if _X11.click(float(xi), float(yi), dwell):
            return True
        await asyncio.sleep(0.05)
        if _X11.click(float(xi), float(yi), dwell):
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


async def _submitted_now(tab) -> bool:
    try:
        val = await tab.evaluate("window.BV_SUBMITTED === true", return_by_value=True)
        if hasattr(val, "value"):
            val = val.value
        return val is True
    except Exception:
        return False


async def _await_submitted_brief(tab, seconds: float = 1.6) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if await _submitted_now(tab):
            return True
        await asyncio.sleep(0.12)
    return False


def _start_point(width: float, height: float, quad: str) -> tuple[float, float]:
    margin = 70.0
    mid_x, mid_y = width * 0.5, height * 0.45
    if quad == "tl":
        return (
            float(random.randint(int(margin), max(int(margin) + 1, int(width * 0.35)))),
            float(random.randint(int(margin), max(int(margin) + 1, int(height * 0.35)))),
        )
    if quad == "tr":
        return (
            float(random.randint(int(width * 0.55), max(int(width * 0.55) + 1, int(width - margin)))),
            float(random.randint(int(margin), max(int(margin) + 1, int(height * 0.35)))),
        )
    if quad == "bl":
        return (
            float(random.randint(int(margin), max(int(margin) + 1, int(width * 0.4)))),
            float(random.randint(int(height * 0.5), max(int(height * 0.5) + 1, int(height - margin)))),
        )
    if quad == "br":
        return (
            float(random.randint(int(width * 0.55), max(int(width * 0.55) + 1, int(width - margin)))),
            float(random.randint(int(height * 0.5), max(int(height * 0.5) + 1, int(height - margin)))),
        )
    return (
        float(mid_x + random.randint(-80, 80)),
        float(mid_y + random.randint(-60, 60)),
    )


def _session_plan(width: float, height: float, session: dict) -> dict:
    """Per-session structural recipe — twin runs must not share stroke topology."""
    wander_lo, wander_hi = session.get("wander_count", [0, 3])
    plan = {
        "start_quad": random.choice(["tl", "tr", "bl", "br", "mid"]),
        "wander_n": random.randint(int(wander_lo), int(wander_hi)),
        "scroll_slot": random.choice(["early", "mid", "late"]),
        "approach": random.choice(["wide_arc", "direct", "side", "undershoot"]),
        "approach_side": random.choice(["left", "right", "above", "below"]),
        "second_wander": random.random() < 0.35,
        "hesitation": random.random() < 0.4,
    }
    print(
        f"[bot] session_plan quad={plan['start_quad']} wander={plan['wander_n']} "
        f"scroll={plan['scroll_slot']} approach={plan['approach']}/{plan['approach_side']}",
        flush=True,
    )
    return plan


async def _wander_strokes(
    tab,
    cursor: tuple[float, float],
    width: float,
    height: float,
    n: int,
    profile: dict,
    budget: MoveBudget,
    pacer: MovePacer,
    pixels: PixelCursor,
) -> tuple[float, float]:
    session = profile["session"]
    for _ in range(max(0, n)):
        if budget.left < 36:
            break
        dest = (
            float(random.randint(60, max(61, int(width - 60)))),
            float(random.randint(60, max(61, int(height - 60)))),
        )
        cursor = await _path(tab, cursor, dest, profile, budget, pacer, pixels, slowdown=1.0)
        pause = _sample_band(session["inter_stroke_pause_ms"], lo=120.0, hi=900.0) / 1000.0
        await asyncio.sleep(min(pause, 0.65))
    return cursor


async def _approach_button(
    tab,
    cursor: tuple[float, float],
    cx: float,
    cy: float,
    bw: float,
    bh: float,
    plan: dict,
    profile: dict,
    budget: MoveBudget,
    pacer: MovePacer,
    pixels: PixelCursor,
    *,
    iw: float = 0.0,
    ih: float = 0.0,
) -> tuple[tuple[float, float], tuple[int, int]]:
    session = profile["session"]
    slow = 1.0 / max(0.2, _sample_band(session["approach_slowdown"], lo=0.2, hi=0.9))
    style = plan["approach"]
    side = plan["approach_side"]
    iw = float(iw or getattr(_X11, "_iw", 0.0) or 0.0)
    ih = float(ih or getattr(_X11, "_ih", 0.0) or 0.0)

    def _in_view(x: float, y: float, pad: float = 12.0) -> tuple[float, float]:
        xi, yi = _clamp_css_to_viewport(x, y, iw, ih, pad=pad)
        return float(xi), float(yi)

    if style == "wide_arc":
        outside = _in_view(float(cx - random.randint(70, 120)), float(cy - random.randint(40, 90)))
        cursor = await _path(tab, cursor, outside, profile, budget, pacer, pixels, slowdown=1.15)
        near = _in_view(float(cx - random.randint(10, 18)), float(cy - random.randint(8, 14)))
        cursor = await _path(tab, cursor, near, profile, budget, pacer, pixels, slowdown=slow)
    elif style == "direct":
        near = _in_view(float(cx + random.randint(-22, 22)), float(cy + random.randint(-28, -10)))
        cursor = await _path(tab, cursor, near, profile, budget, pacer, pixels, slowdown=slow)
    elif style == "side":
        if side == "left":
            gate = _in_view(float(cx - random.randint(50, 90)), float(cy + random.randint(-20, 20)))
        elif side == "right":
            gate = _in_view(float(cx + random.randint(50, 90)), float(cy + random.randint(-20, 20)))
        elif side == "above":
            gate = _in_view(float(cx + random.randint(-25, 25)), float(cy - random.randint(50, 90)))
        else:
            gate = _in_view(float(cx + random.randint(-25, 25)), float(cy + random.randint(40, 70)))
        cursor = await _path(tab, cursor, gate, profile, budget, pacer, pixels, slowdown=1.05)
        near = _in_view(float(cx + random.randint(-12, 12)), float(cy + random.randint(-10, 10)))
        cursor = await _path(tab, cursor, near, profile, budget, pacer, pixels, slowdown=slow)
    else:  # undershoot
        short = _in_view(
            float(cx + random.randint(-8, 8) - random.choice([-1, 1]) * random.randint(24, 40)),
            float(cy + random.randint(-8, 8) - random.choice([-1, 1]) * random.randint(18, 32)),
        )
        cursor = await _path(tab, cursor, short, profile, budget, pacer, pixels, slowdown=1.1)
        await asyncio.sleep(random.uniform(0.08, 0.2))

    dest = _in_view(
        float(cx + random.randint(-int(bw * 0.22), int(bw * 0.22) or 1)),
        float(cy + random.randint(-int(bh * 0.22), int(bh * 0.22) or 1)),
        pad=8.0,
    )
    dest_i = _css_px(*dest)
    cursor = await _path(
        tab,
        cursor,
        (float(dest_i[0]), float(dest_i[1])),
        profile,
        budget,
        pacer,
        pixels,
        slowdown=slow * 1.15,
    )
    return cursor, dest_i

async def _x11_refit_to_css(tab, css_x: float, css_y: float) -> None:
    """Nudge X11 origin using live client coords from the NewDocument fit slot."""
    if not (_X11.ok and _X11._ready):
        return
    iw = float(getattr(_X11, "_iw", 0.0) or 0.0)
    ih = float(getattr(_X11, "_ih", 0.0) or 0.0)
    if iw > 0 and ih > 0:
        if not (8.0 <= float(css_x) <= iw - 8.0 and 8.0 <= float(css_y) <= ih - 8.0):
            return
    try:
        got = await tab.evaluate(
            "document[Symbol.for('bv.mm')] && document[Symbol.for('bv.mm')].xy",
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
        dpr = float(getattr(_X11, "_dpr", 1.0) or 1.0)
        root_x = _X11._ox + gx * dpr
        root_y = _X11._oy + gy * dpr
        _X11._ox = int(round(root_x - float(css_x) * dpr))
        _X11._oy = int(round(root_y - float(css_y) * dpr))
    except Exception:
        pass


async def _read_verify_box(tab) -> dict | None:
    """Live #verify-button box in CSS viewport coords."""
    try:
        raw = await tab.evaluate(
            """(() => {
              const el = document.querySelector('#verify-button');
              if (!el) return null;
              const r = el.getBoundingClientRect();
              return {
                cx: r.left + r.width / 2,
                cy: r.top + r.height / 2,
                w: r.width,
                h: r.height,
                top: r.top,
                bottom: r.bottom,
                iw: window.innerWidth || 0,
                ih: window.innerHeight || 0
              };
            })()""",
            return_by_value=True,
        )
        if hasattr(raw, "value"):
            raw = raw.value
        if not isinstance(raw, dict):
            return None
        return {k: float(raw[k]) for k in ("cx", "cy", "w", "h", "top", "bottom", "iw", "ih")}
    except Exception:
        return None


def _box_on_screen(box: dict, pad: float = 36.0) -> bool:
    iw = float(box.get("iw") or 0.0)
    ih = float(box.get("ih") or 0.0)
    if iw < 80 or ih < 80:
        return False
    cx, cy = float(box["cx"]), float(box["cy"])
    return (
        float(box.get("w") or 0.0) > 2.0
        and float(box.get("h") or 0.0) > 2.0
        and pad <= cx <= iw - pad
        and pad <= cy <= ih - pad
    )


async def _ensure_button_visible(tab) -> dict | None:
    """Bring #verify-button into the viewport (muted) so X11 CSS coords stay valid."""
    box = await _read_verify_box(tab)
    if box and _box_on_screen(box):
        return box
    for attempt in range(1, 5):
        await _set_input_mute(tab, True)
        try:
            await tab.evaluate(
                """(() => {
                  const el = document.querySelector('#verify-button');
                  if (!el) return false;
                  el.scrollIntoView({block: 'center', inline: 'nearest', behavior: 'instant'});
                  return true;
                })()""",
                return_by_value=True,
            )
        except Exception:
            pass
        await asyncio.sleep(0.07)
        await _set_input_mute(tab, False)
        if _X11.ok and _X11._ready:
            _X11.focus_browser()
        box = await _read_verify_box(tab)
        if box and _box_on_screen(box):
            print(
                f"[bot] verify-button on-screen after recover#{attempt} "
                f"center=({box['cx']:.0f},{box['cy']:.0f})",
                flush=True,
            )
            return box
    print(f"[bot] verify-button still off-screen box={box}", flush=True)
    return box


def _clamp_css_to_viewport(x: float, y: float, iw: float, ih: float, pad: float = 8.0) -> tuple[int, int]:
    if iw > 0 and ih > 0:
        x = max(pad, min(iw - pad, float(x)))
        y = max(pad, min(ih - pad, float(y)))
    return _css_px(x, y)


async def _button_click_pure(
    tab,
    button,
    dest: tuple[int, int],
    profile: dict,
    pacer: MovePacer,
    pixels: PixelCursor,
) -> None:
    """X11 click with retries; CDP only as final last resort."""
    path_max = float(profile["path"].get("max_step_px", 32.0))
    session = profile["session"]
    iw = float(getattr(_X11, "_iw", 0.0) or 0.0)
    ih = float(getattr(_X11, "_ih", 0.0) or 0.0)

    async def _resolve_dest() -> tuple[int, int]:
        box = await _ensure_button_visible(tab)
        if box and _box_on_screen(box, pad=24.0):
            bx = float(box["cx"]) + random.uniform(-box["w"] * 0.12, box["w"] * 0.12)
            by = float(box["cy"]) + random.uniform(-box["h"] * 0.12, box["h"] * 0.12)
            return _clamp_css_to_viewport(bx, by, float(box["iw"]), float(box["ih"]))
        try:
            pos = await button.get_position()
            if pos and pos.center:
                bx = float(pos.center[0]) + random.uniform(-pos.width * 0.12, pos.width * 0.12)
                by = float(pos.center[1]) + random.uniform(-pos.height * 0.12, pos.height * 0.12)
                return _clamp_css_to_viewport(bx, by, iw, ih)
        except Exception:
            pass
        return _clamp_css_to_viewport(float(dest[0]), float(dest[1]), iw, ih)

    hover = _sample_band(session["pre_click_hover_ms"], lo=80.0, hi=450.0) / 1000.0
    await asyncio.sleep(hover + random.uniform(0.1, 0.3))
    j_lo, j_hi = session["micro_jitter_n"]
    cur_dest = await _resolve_dest()
    for _ in range(random.randint(j_lo, j_hi)):
        jx = cur_dest[0] + random.choice([-1, 0, 0, 1])
        jy = cur_dest[1] + random.choice([-1, 0, 0, 1])
        jx, jy = _clamp_css_to_viewport(jx, jy, iw, ih)
        await _moved(tab, jx, jy, pacer, pixels, max_step_px=path_max)
        await asyncio.sleep(random.uniform(0.03, 0.06))

    for attempt in range(1, 6 if CDP_DARK else 4):
        cur_dest = await _resolve_dest()
        if attempt > 1:
            _X11.focus_browser()
            await asyncio.sleep(0.05)
        await _moved(tab, cur_dest[0], cur_dest[1], pacer, pixels, max_step_px=path_max)
        await asyncio.sleep(0.04)
        await _x11_refit_to_css(tab, float(cur_dest[0]), float(cur_dest[1]))
        await _moved(tab, cur_dest[0], cur_dest[1], pacer, pixels, max_step_px=path_max)
        ok = await _click_at(tab, cur_dest[0], cur_dest[1], profile, allow_cdp=False)
        if ok and await _await_submitted_brief(tab, 2.0):
            return
        print(
            f"[bot] X11 click attempt {attempt} miss at {cur_dest} — retrying",
            flush=True,
        )
        await asyncio.sleep(random.uniform(0.12, 0.28))

    if CDP_DARK:
        box = await _ensure_button_visible(tab)
        if box and _box_on_screen(box, pad=16.0):
            cur_dest = _clamp_css_to_viewport(box["cx"], box["cy"], box["iw"], box["ih"])
        else:
            cur_dest = await _resolve_dest()
        print(f"[bot] CDP-dark final X11 center shot at {cur_dest}", flush=True)
        await _moved(tab, cur_dest[0], cur_dest[1], pacer, pixels, max_step_px=path_max)
        await _x11_refit_to_css(tab, float(cur_dest[0]), float(cur_dest[1]))
        await _moved(tab, cur_dest[0], cur_dest[1], pacer, pixels, max_step_px=path_max)
        await _click_at(tab, cur_dest[0], cur_dest[1], profile, allow_cdp=False)
        if not await _await_submitted_brief(tab, 2.5):
            raise RuntimeError("CDP-dark: X11 click never submitted")
        return

    cur_dest = await _resolve_dest()
    print(f"[bot] click miss — CDP last resort at {cur_dest}", flush=True)
    await _moved(tab, cur_dest[0], cur_dest[1], pacer, pixels, max_step_px=path_max)
    await _click_at(tab, cur_dest[0], cur_dest[1], profile, allow_cdp=True)
    await _await_submitted_brief(tab, 2.0)

async def _ensure_scrollable(tab) -> None:
    """Make short pages scrollable without a fingerprintable pad id."""
    try:
        await tab.evaluate(
            """
(() => {
  const doc = document.documentElement;
  const body = document.body;
  if (!body) return false;
  if ((doc.scrollHeight || 0) > (window.innerHeight || 0) + 8) return false;
  body.style.minHeight = Math.max(
    parseInt(body.style.minHeight || '0', 10) || 0,
    (window.innerHeight || 800) + 920
  ) + 'px';
  return true;
})();
""",
            return_by_value=True,
        )
    except Exception:
        pass


async def _wheel_tick(tab, cx: float, cy: float, dy: float) -> bool:
    """One wheel notch via CDP Input (trusted, discrete).

    X11 button4/5 was A/B'd under CDP_DARK and cut activity 0.936→0.864
    (smooth-scroll intermediate rows). Keep CDP for wheel only.
    """
    try:
        await tab.send(
            cdp.input_.dispatch_mouse_event(
                "mouseWheel",
                x=float(cx),
                y=float(cy),
                delta_x=0.0,
                delta_y=float(dy),
            )
        )
        return True
    except Exception:
        if _X11.ok and _X11._ready:
            return _X11.wheel(cx, cy, dy)
        return False


async def _human_scroll(tab, cursor: tuple[float, float], profile: dict) -> None:
    """A few trusted wheel ticks (down then partial up) — not a flood."""
    await _ensure_scrollable(tab)
    cfg = profile["scroll"]
    cx, cy = _css_px(*cursor)
    cy = max(80, min(cy, 420))
    ticks_down = random.randint(*cfg.get("down_ticks", [2, 3]))
    ticks_up = random.randint(*cfg.get("up_ticks", [0, 1]))
    ticks_up = min(ticks_up, max(0, 4 - ticks_down))
    gap = cfg.get("gap_ms", [55, 140])
    tick_choices = list(cfg.get("tick_dy", [48, 64, 80, 96]))
    for _ in range(ticks_down):
        dy = float(random.choice(tick_choices))
        if not await _wheel_tick(tab, float(cx), float(cy), dy):
            break
        await asyncio.sleep(random.uniform(*gap) / 1000.0)
        if random.random() < 0.12:
            await asyncio.sleep(random.uniform(0.1, 0.22))
    if ticks_up:
        await asyncio.sleep(random.uniform(*cfg.get("burst_pause_ms", [160, 340])) / 1000.0)
    for _ in range(ticks_up):
        dy = -float(random.choice(tick_choices[:3] or [48]))
        if not await _wheel_tick(tab, float(cx), float(cy), dy):
            break
        await asyncio.sleep(random.uniform(*gap) / 1000.0)
    await _ensure_button_visible(tab)


async def _human_pointer(tab, button, profile: dict = MOTION_PROFILE) -> None:
    _X11._ready = False
    if not await _X11.calibrate(tab):
        if CDP_DARK:
            raise RuntimeError("CDP-dark requires X11/xdotool — calibrate failed")
        print("[bot] X11 unavailable — falling back to CDP mouse (detector-visible)", flush=True)
        try:
            metrics = await tab.evaluate(
                "[window.innerWidth||1440, window.innerHeight||900]",
                return_by_value=True,
            )
            width, height = float(metrics[0]), float(metrics[1])
        except Exception:
            width, height = 1440.0, 900.0
    else:
        width = float(getattr(_X11, "_iw", 1440.0) or 1440.0)
        height = float(getattr(_X11, "_ih", 900.0) or 900.0)

    session = profile["session"]
    plan = _session_plan(width, height, session)
    budget = MoveBudget(int(session.get("max_moves_budget", 95)))
    pacer = MovePacer(float(profile["path"].get("min_move_dt_ms", 14.0)))
    pixels = PixelCursor()
    path_max = float(profile["path"].get("max_step_px", 32.0))
    cursor = _start_point(width, height, plan["start_quad"])

    await _set_input_mute(tab, True)
    park = _densify_steps([(20, 20), _css_px(*cursor)], path_max)
    for x, y in park:
        await _moved(tab, x, y, pacer, pixels, max_step_px=path_max)
        await asyncio.sleep(random.uniform(0.012, 0.028))
    cursor = pixels.pos or cursor
    await _set_input_mute(tab, False)
    budget.take(1)
    await asyncio.sleep(random.uniform(0.15, 0.45))

    scrolled = False

    async def do_scroll() -> None:
        nonlocal cursor, scrolled
        if scrolled:
            return
        await _human_scroll(tab, cursor, profile)
        scrolled = True

    if plan["scroll_slot"] == "early":
        await do_scroll()

    wander_n = int(plan["wander_n"])
    if plan["second_wander"] and wander_n >= 2:
        first = max(1, wander_n // 2)
        cursor = await _wander_strokes(
            tab, cursor, width, height, first, profile, budget, pacer, pixels
        )
        if plan["scroll_slot"] == "mid":
            await do_scroll()
        if plan["hesitation"]:
            await asyncio.sleep(random.uniform(0.18, 0.55))
        cursor = await _wander_strokes(
            tab, cursor, width, height, wander_n - first, profile, budget, pacer, pixels
        )
    else:
        cursor = await _wander_strokes(
            tab, cursor, width, height, wander_n, profile, budget, pacer, pixels
        )
        if plan["scroll_slot"] == "mid":
            await do_scroll()
        if plan["hesitation"]:
            await asyncio.sleep(random.uniform(0.15, 0.45))

    if plan["scroll_slot"] == "late" or not scrolled:
        await do_scroll()

    box = await _ensure_button_visible(tab)
    if not box or not _box_on_screen(box, pad=16.0):
        pos = await button.get_position()
        if not pos or not pos.center:
            raise RuntimeError("no button position")
        cx, cy = float(pos.center[0]), float(pos.center[1])
        bw = max(4.0, float(pos.width))
        bh = max(4.0, float(pos.height))
    else:
        cx, cy = float(box["cx"]), float(box["cy"])
        bw = max(4.0, float(box["w"]))
        bh = max(4.0, float(box["h"]))
        width = float(box["iw"] or width)
        height = float(box["ih"] or height)

    cx, cy = _clamp_css_to_viewport(cx, cy, width, height, pad=24.0)
    cursor, dest = await _approach_button(
        tab,
        cursor,
        float(cx),
        float(cy),
        bw,
        bh,
        plan,
        profile,
        budget,
        pacer,
        pixels,
        iw=width,
        ih=height,
    )
    dest = _clamp_css_to_viewport(float(dest[0]), float(dest[1]), width, height, pad=8.0)
    await _button_click_pure(tab, button, dest, profile, pacer, pixels)


async def _wait_submitted(tab, timeout=25.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await _submitted_now(tab):
            return True
        await asyncio.sleep(0.2)
    return False


FP_PROFILES = [
    {
        "id": "nvidia-1080-linux",
        "hardwareConcurrency": 8,
        "deviceMemory": 8,
        "webglVendor": "Google Inc. (NVIDIA Corporation)",
        "webglRenderer": (
            "ANGLE (NVIDIA Corporation, NVIDIA GeForce GTX 1080/PCIe/SSE2, "
            "OpenGL 4.5.0 NVIDIA 535.183.01)"
        ),
    },
    {
        "id": "nvidia-3060-linux",
        "hardwareConcurrency": 12,
        "deviceMemory": 8,
        "webglVendor": "Google Inc. (NVIDIA Corporation)",
        "webglRenderer": (
            "ANGLE (NVIDIA Corporation, NVIDIA GeForce RTX 3060/PCIe/SSE2, "
            "OpenGL 4.5.0 NVIDIA 550.144.03)"
        ),
    },
    {
        "id": "intel-uhd-620-mesa",
        "hardwareConcurrency": 8,
        "deviceMemory": 8,
        "webglVendor": "Google Inc. (Intel)",
        "webglRenderer": (
            "ANGLE (Intel, Mesa Intel(R) UHD Graphics 620 (KBL GT2), OpenGL 4.6)"
        ),
    },
    {
        "id": "amd-5600-radv",
        "hardwareConcurrency": 16,
        "deviceMemory": 8,
        "webglVendor": "Google Inc. (AMD)",
        "webglRenderer": (
            "ANGLE (AMD, AMD Radeon RX 5600 XT (radeonsi, navi10, LLVM 15.0.7), "
            "OpenGL 4.6)"
        ),
    },
    {
        "id": "intel-iris-xe-mesa",
        "hardwareConcurrency": 8,
        "deviceMemory": 8,
        "webglVendor": "Google Inc. (Intel)",
        "webglRenderer": (
            "ANGLE (Intel, Mesa Intel(R) Iris(R) Xe Graphics (TGL GT2), OpenGL 4.6)"
        ),
    },
]


def _build_env_spoof(profile: dict, viewport: dict) -> str:
    """Early document script: nav + screen/window + WebGL + windowProps scrub."""
    hw = int(profile["hardwareConcurrency"])
    mem = int(profile["deviceMemory"])
    vendor = json.dumps(profile["webglVendor"])
    renderer = json.dumps(profile["webglRenderer"])
    sw = int(viewport["screen_w"])
    sh = int(viewport["screen_h"])
    aw = int(viewport["avail_w"])
    ah = int(viewport["avail_h"])
    ow = int(viewport["win_w"])
    oh = int(viewport["win_h"])
    return f"""
(() => {{
  // Idempotency without leaking a windowProps name (no window.__BV_*).
  try {{
    const flag = Symbol.for('bv.env.spoof.v1');
    if (document[flag]) return;
    Object.defineProperty(document, flag, {{
      value: 1, enumerable: false, configurable: false, writable: false,
    }});
  }} catch (e) {{
    try {{ if (document.documentElement.dataset.bvs === '1') return;
      document.documentElement.dataset.bvs = '1'; }} catch (e2) {{}}
  }}
  const spoof = (obj, prop, value) => {{
    try {{
      Object.defineProperty(obj, prop, {{
        get: () => value,
        configurable: true,
      }});
    }} catch (e) {{}}
  }};
  spoof(Navigator.prototype, 'hardwareConcurrency', {hw});
  spoof(Navigator.prototype, 'deviceMemory', {mem});
  spoof(Navigator.prototype, 'webdriver', undefined);
  spoof(Navigator.prototype, 'platform', 'Linux x86_64');
  spoof(Navigator.prototype, 'languages', Object.freeze(['en-US', 'en']));
  spoof(Navigator.prototype, 'language', 'en-US');
  spoof(Navigator.prototype, 'maxTouchPoints', 0);
  spoof(Navigator.prototype, 'vendor', 'Google Inc.');

  // Align Notification.permission vocabulary with permissions.query state.
  // Chrome natively reports permission="default" but query state="prompt".
  // MetricsProcessor treats that mismatch as an activity penalty (0.936 vs 1.0).
  try {{
    if (navigator.permissions && typeof navigator.permissions.query === 'function') {{
      const perms = navigator.permissions;
      const proto = Object.getPrototypeOf(perms);
      const target = (proto && typeof proto.query === 'function')
        ? proto.query
        : perms.query;
      const proxied = new Proxy(target, {{
        apply(fn, thisArg, args) {{
          const ret = Reflect.apply(fn, thisArg, args);
          return Promise.resolve(ret).then((status) => {{
            try {{
              const desc = args && args[0];
              const name = desc && desc.name;
              if (name === 'notifications' || name === 'push') {{
                const aligned =
                  (typeof Notification !== 'undefined' && Notification.permission) ||
                  'default';
                return new Proxy(status, {{
                  get(obj, prop, recv) {{
                    if (prop === 'state') return aligned;
                    const v = Reflect.get(obj, prop, recv);
                    return typeof v === 'function' ? v.bind(obj) : v;
                  }},
                }});
              }}
            }} catch (e) {{}}
            return status;
          }});
        }},
      }});
      try {{
        if (proto && typeof proto.query === 'function') {{
          Object.defineProperty(proto, 'query', {{
            configurable: true, enumerable: true, writable: true, value: proxied,
          }});
        }} else {{
          perms.query = proxied;
        }}
      }} catch (e) {{
        try {{ perms.query = proxied; }} catch (e2) {{}}
      }}
    }}
  }} catch (e) {{}}

  // Screen / outer window: avoid fixed automation desktop fingerprint.
  // Do NOT spoof innerWidth/innerHeight — layout + CDP clicks need the real viewport.
  try {{
    const screenProto = window.Screen && window.Screen.prototype;
    if (screenProto) {{
      spoof(screenProto, 'width', {sw});
      spoof(screenProto, 'height', {sh});
      spoof(screenProto, 'availWidth', {aw});
      spoof(screenProto, 'availHeight', {ah});
      spoof(screenProto, 'availLeft', 0);
      spoof(screenProto, 'availTop', 0);
      spoof(screenProto, 'colorDepth', 24);
      spoof(screenProto, 'pixelDepth', 24);
    }}
    spoof(window, 'outerWidth', {ow});
    spoof(window, 'outerHeight', {oh});
    spoof(window, 'screenX', {int(viewport.get("pos_x", 0))});
    spoof(window, 'screenY', {int(viewport.get("pos_y", 0))});
    spoof(window, 'screenLeft', {int(viewport.get("pos_x", 0))});
    spoof(window, 'screenTop', {int(viewport.get("pos_y", 0))});
    spoof(window, 'devicePixelRatio', 1);
  }} catch (e) {{}}

  try {{
    window.chrome = window.chrome || {{
      runtime: {{}}, app: {{}}, csi: () => ({{}}), loadTimes: () => ({{}}),
    }};
  }} catch (e) {{}}

  // pageLoadMs: collector reads navigation entry duration (often 0 under CDP).
  try {{
    const fakeMs = 850 + Math.floor(Math.random() * 900);
    const proto = Performance && Performance.prototype;
    if (proto && typeof proto.getEntriesByType === 'function' && !proto.getEntriesByType.__bvPerfProxied) {{
      const orig = proto.getEntriesByType;
      const proxied = new Proxy(orig, {{
        apply(target, thisArg, args) {{
          const entries = Reflect.apply(target, thisArg, args);
          if (!args.length || args[0] !== 'navigation' || !entries || !entries.length) return entries;
          const e0 = entries[0];
          const duration = (e0 && typeof e0.duration === 'number' && e0.duration > 1) ? e0.duration : fakeMs;
          const wrapped = new Proxy(e0, {{
            get(t, prop, recv) {{
              if (prop === 'duration') return duration;
              const v = Reflect.get(t, prop, recv);
              return typeof v === 'function' ? v.bind(t) : v;
            }},
          }});
          return [wrapped];
        }},
      }});
      try {{ Object.defineProperty(proxied, '__bvPerfProxied', {{ value: true }}); }} catch (e) {{}}
      try {{
        Object.defineProperty(proto, 'getEntriesByType', {{
          configurable: true, enumerable: true, writable: true, value: proxied,
        }});
      }} catch (e) {{
        try {{ proto.getEntriesByType = proxied; }} catch (e2) {{}}
      }}
    }}
  }} catch (e) {{}}

  // WebGL identity via Proxy(native getParameter): toString stays [native code].
  const GL_VENDOR = {vendor};
  const GL_RENDERER = {renderer};
  const patchGL = (proto) => {{
    if (!proto || typeof proto.getParameter !== 'function') return;
    if (proto.getParameter.__bvGlProxied) return;
    const orig = proto.getParameter;
    const proxied = new Proxy(orig, {{
      apply(target, thisArg, args) {{
        const param = args.length ? args[0] : undefined;
        // UNMASKED_VENDOR_WEBGL / UNMASKED_RENDERER_WEBGL
        if (param === 0x9245 || param === 37445) return GL_VENDOR;
        if (param === 0x9246 || param === 37446) return GL_RENDERER;
        try {{
          if (thisArg && param === thisArg.MAX_TEXTURE_SIZE) {{
            const v = Reflect.apply(target, thisArg, args);
            if (v && v <= 8192) return 16384;
            return v;
          }}
        }} catch (e) {{}}
        return Reflect.apply(target, thisArg, args);
      }},
    }});
    try {{
      Object.defineProperty(proxied, '__bvGlProxied', {{ value: true }});
    }} catch (e) {{}}
    try {{
      Object.defineProperty(proto, 'getParameter', {{
        configurable: true,
        enumerable: true,
        writable: true,
        value: proxied,
      }});
    }} catch (e) {{
      try {{ proto.getParameter = proxied; }} catch (e2) {{}}
    }}
  }};
  try {{ patchGL(WebGLRenderingContext && WebGLRenderingContext.prototype); }} catch (e) {{}}
  try {{ patchGL(WebGL2RenderingContext && WebGL2RenderingContext.prototype); }} catch (e) {{}}

  // Pad WebGL extensions (SwiftShader reports ~36; desktop Mesa is higher).
  try {{
    const EXTRA = [
      'EXT_color_buffer_float','EXT_float_blend','EXT_texture_compression_bptc',
      'EXT_texture_compression_rgtc','EXT_texture_filter_anisotropic',
      'EXT_texture_norm16','KHR_parallel_shader_compile','OES_draw_buffers_indexed',
      'OES_texture_float_linear','OVR_multiview2','WEBGL_compressed_texture_s3tc',
      'WEBGL_compressed_texture_s3tc_srgb','WEBGL_multi_draw',
      'EXT_color_buffer_half_float','EXT_disjoint_timer_query_webgl2',
      'EXT_texture_mirror_clamp_to_edge',
    ];
    const patchExt = (proto) => {{
      if (!proto || typeof proto.getSupportedExtensions !== 'function') return;
      if (proto.getSupportedExtensions.__bvExtProxied) return;
      const orig = proto.getSupportedExtensions;
      const proxied = new Proxy(orig, {{
        apply(target, thisArg, args) {{
          const list = Reflect.apply(target, thisArg, args) || [];
          const out = list.slice();
          for (let i = 0; i < EXTRA.length; i++) {{
            if (out.indexOf(EXTRA[i]) === -1) out.push(EXTRA[i]);
          }}
          return out;
        }},
      }});
      try {{ Object.defineProperty(proxied, '__bvExtProxied', {{ value: true }}); }} catch (e) {{}}
      try {{
        Object.defineProperty(proto, 'getSupportedExtensions', {{
          configurable: true, enumerable: true, writable: true, value: proxied,
        }});
      }} catch (e) {{
        try {{ proto.getSupportedExtensions = proxied; }} catch (e2) {{}}
      }}
    }};
    patchExt(WebGLRenderingContext && WebGLRenderingContext.prototype);
    patchExt(WebGL2RenderingContext && WebGL2RenderingContext.prototype);
  }} catch (e) {{}}

  // Mute pointer/mouse to the collector until the bot parks the cursor.
  // Fit listener is registered FIRST so active-fit still reads client coords.
  try {{
    const muteKey = Symbol.for('bv.input.mute');
    const fitKey = Symbol.for('bv.mm');
    const mute = {{ on: true }};
    const fit = {{ xy: null }};
    Object.defineProperty(document, muteKey, {{
      value: mute, enumerable: false, configurable: false,
    }});
    Object.defineProperty(document, fitKey, {{
      value: fit, enumerable: false, configurable: true,
    }});
    const onFit = (e) => {{ fit.xy = [e.clientX, e.clientY]; }};
    window.addEventListener('pointermove', onFit, true);
    window.addEventListener('mousemove', onFit, true);
    const onMute = (e) => {{
      if (!mute.on) return;
      try {{
        e.stopImmediatePropagation();
        e.stopPropagation();
      }} catch (err) {{}}
    }};
    window.addEventListener('pointermove', onMute, true);
    window.addEventListener('mousemove', onMute, true);
    document.addEventListener('pointermove', onMute, true);
    document.addEventListener('mousemove', onMute, true);
    // Recovery scrollIntoView must not add a 5th trusted scroll row (activity≈0.929).
    window.addEventListener('scroll', onMute, true);
    document.addEventListener('scroll', onMute, true);
    window.addEventListener('wheel', onMute, true);
    document.addEventListener('wheel', onMute, true);
  }} catch (e) {{}}

  // Cap focus/blur without wrapping addEventListener (that broke twin-session /_eval).
  try {{
    let focusSeen = 0;
    const stopExtra = (ev) => {{
      try {{
        if (ev.type === 'blur') {{
          ev.stopImmediatePropagation();
          ev.stopPropagation();
          return;
        }}
        focusSeen += 1;
        if (focusSeen > 1) {{
          ev.stopImmediatePropagation();
          ev.stopPropagation();
        }}
      }} catch (e) {{}}
    }};
    window.addEventListener('focus', stopExtra, true);
    window.addEventListener('blur', stopExtra, true);
    document.addEventListener('focus', stopExtra, true);
    document.addEventListener('blur', stopExtra, true);
  }} catch (e) {{}}

  // A/B move dedup: drop one of Chrome's paired pointer/mouse move streams so the
  // collector does not see same-t duplicates. Must register before page scripts.
  try {{
    const dedup = {json.dumps(MOVE_DEDUP_MODE)};
    if (dedup === 'suppress_mouse' || dedup === 'suppress_pointer') {{
      const typ = dedup === 'suppress_mouse' ? 'mousemove' : 'pointermove';
      const block = (ev) => {{
        try {{
          ev.stopImmediatePropagation();
          ev.stopPropagation();
        }} catch (e) {{}}
      }};
      window.addEventListener(typ, block, true);
      document.addEventListener(typ, block, true);
    }}
  }} catch (e) {{}}

  // Worker UAs: BV probes blob Workers with a 200ms timeout — stub blob workers.
  try {{
    const ua = navigator.userAgent;
    const isBlob = (u) => typeof u === 'string' && u.indexOf('blob:') === 0;
    const OrigWorker = window.Worker;
    if (typeof OrigWorker === 'function' && !OrigWorker.__bvWorkerStub) {{
      const StubWorker = function (url, options) {{
        if (!isBlob(url)) {{
          try {{
            return Reflect.construct(OrigWorker, [url, options], new.target || StubWorker);
          }} catch (e) {{
            return new OrigWorker(url, options);
          }}
        }}
        let handler = null;
        let delivered = false;
        const deliver = () => {{
          if (delivered || typeof handler !== 'function') return;
          delivered = true;
          try {{ handler({{ data: ua }}); }} catch (e) {{}}
        }};
        const fake = {{
          postMessage() {{}},
          terminate() {{}},
          addEventListener(type, fn) {{
            if (type === 'message') {{ handler = fn; queueMicrotask(deliver); }}
          }},
          removeEventListener() {{}},
          dispatchEvent() {{ return false; }},
        }};
        Object.defineProperty(fake, 'onmessage', {{
          configurable: true,
          get() {{ return handler; }},
          set(fn) {{ handler = fn; queueMicrotask(deliver); }},
        }});
        return fake;
      }};
      StubWorker.prototype = OrigWorker.prototype;
      try {{ Object.defineProperty(StubWorker, '__bvWorkerStub', {{ value: true }}); }} catch (e) {{}}
      try {{ Object.defineProperty(StubWorker, 'name', {{ value: 'Worker', configurable: true }}); }} catch (e) {{}}
      window.Worker = StubWorker;
    }}
    const OrigShared = window.SharedWorker;
    if (typeof OrigShared === 'function' && !OrigShared.__bvWorkerStub) {{
      const StubShared = function (url, options) {{
        if (!isBlob(url)) {{
          try {{
            return Reflect.construct(OrigShared, [url, options], new.target || StubShared);
          }} catch (e) {{
            return new OrigShared(url, options);
          }}
        }}
        let handler = null;
        let delivered = false;
        const deliver = () => {{
          if (delivered || typeof handler !== 'function') return;
          delivered = true;
          try {{ handler({{ data: ua }}); }} catch (e) {{}}
        }};
        const port = {{
          postMessage() {{}},
          start() {{ queueMicrotask(deliver); }},
          close() {{}},
          addEventListener(type, fn) {{
            if (type === 'message') {{ handler = fn; }}
          }},
          removeEventListener() {{}},
        }};
        Object.defineProperty(port, 'onmessage', {{
          configurable: true,
          get() {{ return handler; }},
          set(fn) {{ handler = fn; }},
        }});
        return {{ port, terminate() {{}}, close() {{}} }};
      }};
      StubShared.prototype = OrigShared.prototype;
      try {{ Object.defineProperty(StubShared, '__bvWorkerStub', {{ value: true }}); }} catch (e) {{}}
      try {{ Object.defineProperty(StubShared, 'name', {{ value: 'SharedWorker', configurable: true }}); }} catch (e) {{}}
      window.SharedWorker = StubShared;
    }}
  }} catch (e) {{}}

  // Diversify canvas via real pixel noise only (no base64 corruption).
  try {{
    const cproto = HTMLCanvasElement && HTMLCanvasElement.prototype;
    if (cproto && typeof cproto.toDataURL === 'function' && !cproto.toDataURL.__bvCanvasProxied) {{
      const orig = cproto.toDataURL;
      const salt = Math.floor(Math.random() * 250) + 1;
      const salted = new WeakSet();
      const proxied = new Proxy(orig, {{
        apply(target, thisArg, args) {{
          try {{
            if (thisArg && !salted.has(thisArg)) {{
              const ctx2d = thisArg.getContext && thisArg.getContext('2d');
              if (ctx2d) {{
                ctx2d.save();
                ctx2d.globalAlpha = 0.08;
                ctx2d.fillStyle = 'rgb(' + salt + ',' + ((salt * 5) % 255) + ',' + ((salt * 11) % 255) + ')';
                ctx2d.fillRect(Math.max(0, thisArg.width - 3), Math.max(0, thisArg.height - 3), 2, 2);
                ctx2d.globalAlpha = 0.04;
                ctx2d.fillRect(salt % Math.max(1, thisArg.width - 1), (salt * 3) % Math.max(1, thisArg.height - 1), 1, 1);
                ctx2d.restore();
                salted.add(thisArg);
              }}
            }}
          }} catch (e) {{}}
          return Reflect.apply(target, thisArg, args);
        }},
      }});
      try {{ Object.defineProperty(proxied, '__bvCanvasProxied', {{ value: true }}); }} catch (e) {{}}
      try {{
        Object.defineProperty(cproto, 'toDataURL', {{
          configurable: true, enumerable: true, writable: true, value: proxied,
        }});
      }} catch (e) {{
        try {{ cproto.toDataURL = proxied; }} catch (e2) {{}}
      }}
    }}
  }} catch (e) {{}}

  // Activity scorer hard-penalizes these names in browserInfo.windowProps /
  // navigatorProps (collector: getOwnPropertyNames(window|navigator).slice(0,2048)).
  const HIDE_WINDOW = new Set([
    'ImageBitmap',
    'ImageBitmapRenderingContext',
    'createImageBitmap',
    'StorageBucket',
    'StorageBucketManager',
  ]);
  const HIDE_NAV = new Set([
    'webdriver',
    '__webdriver_evaluate',
    '__selenium_evaluate',
    '__driver_evaluate',
    '__webdriver_script_fn',
    '__fxdriver_evaluate',
    '_Selenium_IDE_Recorder',
    '_selenium',
    'callSelenium',
    'calledSelenium',
    '__nightmare',
    '_phantom',
    'phantom',
    'domAutomation',
    'domAutomationController',
  ]);
  for (const name of HIDE_WINDOW) {{
    try {{ delete window[name]; }} catch (e) {{}}
  }}
  const scrubWindow = (names) => names.filter((n) => !HIDE_WINDOW.has(n));
  const scrubNav = (names) => names.filter((n) => !HIDE_NAV.has(n));
  const patchEnum = (obj, key) => {{
    try {{
      const orig = obj[key];
      if (typeof orig !== 'function' || orig.__bvEnumProxied) return;
      const proxied = new Proxy(orig, {{
        apply(target, thisArg, args) {{
          const out = Reflect.apply(target, thisArg, args);
          try {{
            const subject = args[0];
            if (subject === window || subject === globalThis) return scrubWindow(out);
            if (typeof Navigator !== 'undefined' && subject === navigator) return scrubNav(out);
          }} catch (e) {{}}
          return out;
        }},
      }});
      try {{
        Object.defineProperty(proxied, '__bvEnumProxied', {{ value: true }});
      }} catch (e) {{}}
      Object.defineProperty(obj, key, {{
        configurable: true,
        writable: true,
        value: proxied,
      }});
    }} catch (e) {{}}
  }};
  patchEnum(Object, 'getOwnPropertyNames');
  patchEnum(Object, 'keys');
  try {{
    const origKeys = Reflect.ownKeys;
    if (typeof origKeys === 'function' && !origKeys.__bvEnumProxied) {{
      const proxied = new Proxy(origKeys, {{
        apply(target, thisArg, args) {{
          const out = Reflect.apply(target, thisArg, args);
          try {{
            const subject = args[0];
            if (subject === window || subject === globalThis) {{
              return out.filter((n) => typeof n !== 'string' || !HIDE_WINDOW.has(n));
            }}
            if (typeof Navigator !== 'undefined' && subject === navigator) {{
              return out.filter((n) => typeof n !== 'string' || !HIDE_NAV.has(n));
            }}
          }} catch (e) {{}}
          return out;
        }},
      }});
      try {{
        Object.defineProperty(proxied, '__bvEnumProxied', {{ value: true }});
      }} catch (e) {{}}
      Reflect.ownKeys = proxied;
    }}
  }} catch (e) {{}}

}})();
"""


async def _run_async(url: str) -> bool:
    browser = None
    try:
        vp = _pick_viewport()
        _ensure_display(vp["win_w"], vp["win_h"])
        fp = random.choice(FP_PROFILES)
        env_spoof = _build_env_spoof(fp, vp)
        print(
            f"[bot] fingerprint={fp['id']} viewport={vp['id']} "
            f"win={vp['win_w']}x{vp['win_h']} screen={vp['screen_w']}x{vp['screen_h']} "
            f"pos={vp['pos_x']},{vp['pos_y']} move_dedup={MOVE_DEDUP_MODE} "
            f"cdp_dark={int(CDP_DARK)}",
            flush=True,
        )
        browser_args = [
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-dev-shm-usage",
            f"--window-size={vp['win_w']},{vp['win_h']}",
            f"--window-position={vp['pos_x']},{vp['pos_y']}",
            "--ignore-certificate-errors",
            "--use-gl=angle",
            "--use-angle=swiftshader",
            "--enable-webgl",
            "--ignore-gpu-blocklist",
        ]
        if CDP_DARK:
            browser_args.append("--disable-smooth-scrolling")
        browser = await uc.start(
            headless=False,
            browser_executable_path=_chrome_binary(),
            sandbox=False,
            lang="en-US",
            browser_args=browser_args,
        )
        try:
            tab0 = browser.main_tab or (browser.tabs[0] if browser.tabs else None)
            if tab0 is not None:
                await tab0.send(cdp.page.enable())
                await tab0.send(cdp.page.add_script_to_evaluate_on_new_document(env_spoof))
        except Exception as exc:
            print(f"[bot] env spoof hook failed: {exc}", flush=True)

        tab = await browser.get(url)
        await asyncio.sleep(random.uniform(1.8, 2.6))
        try:
            hooked = await tab.evaluate(
                "!!document[Symbol.for('bv.env.spoof.v1')]",
                return_by_value=True,
            )
            if hasattr(hooked, "value"):
                hooked = hooked.value
            if hooked is not True:
                await tab.evaluate(env_spoof, return_by_value=True)
        except Exception:
            try:
                await tab.evaluate(env_spoof, return_by_value=True)
            except Exception:
                pass
        button = await tab.select("#verify-button", timeout=20)
        if button is None:
            raise RuntimeError("verify-button not found")
        await asyncio.sleep(random.uniform(0.5, 1.0))
        await _human_pointer(tab, button, MOTION_PROFILE)
        if not await _wait_submitted(tab):
            raise RuntimeError("BV_SUBMITTED never became true")
        await asyncio.sleep(random.uniform(2.0, 2.8))
        return True
    except Exception as exc:
        print(f"[bot] nodriver run failed: {exc}", flush=True)
        return False
    finally:
        if browser is not None:
            try:
                browser.stop()
            except Exception:
                pass


def run_bot(url: str) -> bool:
    try:
        return asyncio.run(_run_async(url))
    except Exception as exc:
        print(f"[bot] run_bot failed: {exc}", flush=True)
        return False
