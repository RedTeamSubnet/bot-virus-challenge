"""Pointer path synthesis and session recipes."""
from __future__ import annotations

import asyncio
import math
import random

from rtk_cfg import css_px, move_dt_s, sample_band
from rtk_pointer import CssTrail, GapClock, StepWallet, _CURSOR, _moved


def _clamp_css_to_viewport(
    x: float, y: float, iw: float, ih: float, pad: float = 8.0
) -> tuple[int, int]:
    if iw > 0 and ih > 0:
        x = max(pad, min(iw - pad, float(x)))
        y = max(pad, min(ih - pad, float(y)))
    return css_px(x, y)

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
            xi, yi = css_px(lx + dx * t + jx, ly + dy * t + jy)
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
    coarse: list[tuple[int, int]] = [css_px(x0, y0)]
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
            xi, yi = css_px(x, y)
            if coarse[-1] != (xi, yi):
                coarse.append((xi, yi))
    end_i = css_px(x1, y1)
    if coarse[-1] != end_i:
        coarse.append(end_i)
    return _densify_steps(coarse, max_step_px)



async def _path(
    tab,
    start: tuple[float, float],
    end: tuple[float, float],
    profile: dict,
    budget: StepWallet,
    pacer: GapClock,
    cursor: CssTrail,
    slowdown: float = 1.0,
) -> tuple[float, float]:
    path_cfg = profile["path"]
    start_i = css_px(*start)
    end_i = css_px(*end)
    dist = math.hypot(end_i[0] - start_i[0], end_i[1] - start_i[1])
    steps_per_100 = sample_band(path_cfg["steps_per_100px"], lo=3.5, hi=10.0)
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
    ctrl = sample_band(path_cfg["ctrl_offset_frac"], lo=0.02, hi=0.35)
    noise = sample_band(path_cfg["noise_px"], lo=0.0, hi=1.5)
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
        await asyncio.sleep(move_dt_s(path_cfg, pace=pace))
    if sent == 0 or cursor.pos != (float(end_i[0]), float(end_i[1])):
        cur = cursor.pos or (float(start_i[0]), float(start_i[1]))
        settle = _densify_steps(
            [css_px(*cur), end_i],
            float(path_cfg.get("max_step_px", 32.0)),
        )
        for x, y in settle[1:]:
            await _moved(tab, x, y, pacer, cursor, max_step_px=path_max)
            await asyncio.sleep(move_dt_s(path_cfg, pace=slowdown))
    return float(end_i[0]), float(end_i[1])



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


def roll_session_recipe(width: float, height: float, session: dict) -> dict:
    """Per-session structural recipe — twin runs must not share stroke topology."""
    wander_lo, wander_hi = session.get("wander_count", [0, 3])
    quads = ["mid", "br", "tl", "tr", "bl"]
    random.shuffle(quads)
    slots = ["late", "early", "mid"]
    random.shuffle(slots)
    styles = ["direct", "undershoot", "wide_arc", "side"]
    random.shuffle(styles)
    sides = ["below", "left", "above", "right"]
    random.shuffle(sides)
    plan = {
        "start_quad": quads[0],
        "wander_n": random.randint(int(wander_lo), int(wander_hi)),
        "scroll_slot": slots[0],
        "approach": styles[0],
        "approach_side": sides[0],
        "second_wander": random.random() < 0.38,
        "hesitation": random.random() < 0.37,
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
    budget: StepWallet,
    pacer: GapClock,
    pixels: CssTrail,
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
        pause = sample_band(session["inter_stroke_pause_ms"], lo=120.0, hi=900.0) / 1000.0
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
    budget: StepWallet,
    pacer: GapClock,
    pixels: CssTrail,
    *,
    iw: float = 0.0,
    ih: float = 0.0,
) -> tuple[tuple[float, float], tuple[int, int]]:
    session = profile["session"]
    slow = 1.0 / max(0.2, sample_band(session["approach_slowdown"], lo=0.2, hi=0.9))
    style = plan["approach"]
    side = plan["approach_side"]
    iw = float(iw or getattr(_CURSOR, "_iw", 0.0) or 0.0)
    ih = float(ih or getattr(_CURSOR, "_ih", 0.0) or 0.0)

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
    dest_i = css_px(*dest)
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


