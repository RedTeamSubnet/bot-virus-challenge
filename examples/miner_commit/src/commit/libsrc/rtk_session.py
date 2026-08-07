"""Scroll, target acquisition, and click commitment."""
from __future__ import annotations

import asyncio
import random
import time

from nodriver import cdp

from rtk_cfg import OS_POINTER, TRAIL_SPEC, css_px, sample_band
from rtk_motion import (
    _approach_button,
    _densify_steps,
    _path,
    _start_point,
    _wander_strokes,
    roll_session_recipe,
)
from rtk_pointer import (
    CssTrail,
    GapClock,
    StepWallet,
    _CURSOR,
    _click_at,
    _moved,
    _set_input_mute,
    _x11_refit_to_css,
)

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


async def bring_target_into_view(tab) -> dict | None:
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
        if _CURSOR.ok and _CURSOR._ready:
            _CURSOR.focus_browser()
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
    return css_px(x, y)


async def commit_target_click(
    tab,
    button,
    dest: tuple[int, int],
    profile: dict,
    pacer: GapClock,
    pixels: CssTrail,
) -> None:
    """X11 click with retries; CDP only as final last resort."""
    path_max = float(profile["path"].get("max_step_px", 32.0))
    session = profile["session"]
    iw = float(getattr(_CURSOR, "_iw", 0.0) or 0.0)
    ih = float(getattr(_CURSOR, "_ih", 0.0) or 0.0)

    async def _resolve_dest() -> tuple[int, int]:
        box = await bring_target_into_view(tab)
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

    hover = sample_band(session["pre_click_hover_ms"], lo=80.0, hi=450.0) / 1000.0
    await asyncio.sleep(hover + random.uniform(0.1, 0.3))
    j_lo, j_hi = session["micro_jitter_n"]
    cur_dest = await _resolve_dest()
    for _ in range(random.randint(j_lo, j_hi)):
        jx = cur_dest[0] + random.choice([-1, 0, 0, 1])
        jy = cur_dest[1] + random.choice([-1, 0, 0, 1])
        jx, jy = _clamp_css_to_viewport(jx, jy, iw, ih)
        await _moved(tab, jx, jy, pacer, pixels, max_step_px=path_max)
        await asyncio.sleep(random.uniform(0.03, 0.06))

    for attempt in range(1, 6 if OS_POINTER else 4):
        cur_dest = await _resolve_dest()
        if attempt > 1:
            _CURSOR.focus_browser()
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

    if OS_POINTER:
        box = await bring_target_into_view(tab)
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

    X11 button4/5 was A/B'd under OS_POINTER and cut activity 0.936→0.864
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
        if _CURSOR.ok and _CURSOR._ready:
            return _CURSOR.wheel(cx, cy, dy)
        return False


async def wheel_burst(tab, cursor: tuple[float, float], profile: dict) -> None:
    """A few trusted wheel ticks (down then partial up) — not a flood."""
    await _ensure_scrollable(tab)
    cfg = profile["scroll"]
    cx, cy = css_px(*cursor)
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
    await bring_target_into_view(tab)



async def drive_pointer_session(tab, button, profile: dict = TRAIL_SPEC) -> None:
    _CURSOR._ready = False
    if not await _CURSOR.calibrate(tab):
        if OS_POINTER:
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
        width = float(getattr(_CURSOR, "_iw", 1440.0) or 1440.0)
        height = float(getattr(_CURSOR, "_ih", 900.0) or 900.0)

    session = profile["session"]
    plan = roll_session_recipe(width, height, session)
    budget = StepWallet(int(session.get("max_moves_budget", 95)))
    pacer = GapClock(float(profile["path"].get("min_move_dt_ms", 14.0)))
    pixels = CssTrail()
    path_max = float(profile["path"].get("max_step_px", 32.0))
    cursor = _start_point(width, height, plan["start_quad"])

    await _set_input_mute(tab, True)
    park = _densify_steps([(20, 20), css_px(*cursor)], path_max)
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
        await wheel_burst(tab, cursor, profile)
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

    box = await bring_target_into_view(tab)
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
    await commit_target_click(tab, button, dest, profile, pacer, pixels)



async def wait_submitted(tab, timeout=25.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await _submitted_now(tab):
            return True
        await asyncio.sleep(0.2)
    return False



