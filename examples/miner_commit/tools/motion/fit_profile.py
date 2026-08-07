#!/usr/bin/env python3
"""Fit a compact motion_profile.json from AdSERP (and optional PSAI) segments."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from pathlib import Path


def pct(vals: list[float], p: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    if len(s) == 1:
        return float(s[0])
    k = (len(s) - 1) * p / 100.0
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(s[int(k)])
    return float(s[f] + (s[c] - s[f]) * (k - f))


def band(vals: list[float], lo: float = 10, mid: float = 50, hi: float = 90) -> dict:
    return {
        "p10": None if not vals else round(pct(vals, lo), 4),
        "p50": None if not vals else round(pct(vals, mid), 4),
        "p90": None if not vals else round(pct(vals, hi), 4),
        "n": len(vals),
    }


def load_adserp_csvs(root: Path) -> list[list[tuple[float, float, float, str]]]:
    sessions = []
    for path in sorted(root.glob("*.csv")):
        rows = []
        with path.open(newline="") as fh:
            for row in csv.DictReader(fh):
                try:
                    rows.append(
                        (
                            float(row["timestamp"]),
                            float(row["xpos"]),
                            float(row["ypos"]),
                            row["event"],
                        )
                    )
                except (KeyError, TypeError, ValueError):
                    continue
        if rows:
            sessions.append(rows)
    return sessions


def load_psai_segments(root: Path) -> list[list[tuple[float, float, float, str]]]:
    """Load normalized JSON segments from ingest_psai.py if present."""
    sessions = []
    if not root.exists():
        return sessions
    for path in sorted(root.glob("*.json")):
        payload = json.loads(path.read_text())
        events = payload.get("events") or []
        rows = []
        for ev in events:
            kind = ev.get("type")
            if kind == "move":
                event = "mousemove"
            elif kind == "down":
                event = "mousedown"
            elif kind == "up":
                event = "mouseup"
            elif kind == "click":
                event = "click"
            elif kind == "wheel":
                event = "wheel"
            else:
                continue
            rows.append([float(ev["t"]), float(ev["x"]), float(ev["y"]), event])
        if len(rows) < 3:
            continue
        # Convert seconds → ms when needed (older PSAI segment files).
        gaps = [
            rows[i][0] - rows[i - 1][0]
            for i in range(1, min(len(rows), 50))
            if rows[i][0] > rows[i - 1][0]
        ]
        if gaps and sorted(gaps)[len(gaps) // 2] < 5.0:
            for row in rows:
                row[0] *= 1000.0
        sessions.append([(t, x, y, e) for t, x, y, e in rows])
    return sessions


def fit(sessions: list[list[tuple[float, float, float, str]]], sources: list[str]) -> dict:
    dt_ms: list[float] = []
    speed: list[float] = []
    steps_per_100px: list[float] = []
    ctrl_offset: list[float] = []
    pause_ms: list[float] = []
    pre_click_hover: list[float] = []
    click_dwell: list[float] = []
    approach_slowdown: list[float] = []

    for rows in sessions:
        moves = [(t, x, y) for t, x, y, e in rows if e == "mousemove"]
        clicks = [(t, x, y) for t, x, y, e in rows if e in {"click", "mousedown"}]
        downs = [(t, x, y) for t, x, y, e in rows if e == "mousedown"]
        ups = [(t, x, y) for t, x, y, e in rows if e == "mouseup"]

        for d in downs:
            later = [u for u in ups if u[0] >= d[0] and (u[0] - d[0]) < 1000]
            if later:
                click_dwell.append(later[0][0] - d[0])

        if len(moves) < 5:
            continue

        for a, b in zip(moves, moves[1:]):
            gap = b[0] - a[0]
            if 180 < gap < 2000:
                pause_ms.append(gap)

        strokes: list[list[tuple[float, float, float]]] = []
        cur = [moves[0]]
        for a, b in zip(moves, moves[1:]):
            gap = b[0] - a[0]
            if gap > 180:
                if len(cur) >= 4:
                    strokes.append(cur)
                cur = [b]
            else:
                cur.append(b)
                if gap > 0:
                    dt_ms.append(gap)
        if len(cur) >= 4:
            strokes.append(cur)

        for stroke in strokes:
            dists = []
            speeds = []
            for a, b in zip(stroke, stroke[1:]):
                d = math.hypot(b[1] - a[1], b[2] - a[2])
                dt = max(b[0] - a[0], 1e-3)
                dists.append(d)
                spd = d / dt * 1000.0
                speeds.append(spd)
                speed.append(spd)
            path_len = sum(dists)
            if path_len < 15:
                continue
            chord = math.hypot(stroke[-1][1] - stroke[0][1], stroke[-1][2] - stroke[0][2])
            if chord < 10:
                continue
            steps_per_100px.append(len(stroke) / (path_len / 100.0))
            x0, y0 = stroke[0][1], stroke[0][2]
            x1, y1 = stroke[-1][1], stroke[-1][2]
            max_dev = 0.0
            for _, x, y in stroke:
                num = abs((y1 - y0) * x - (x1 - x0) * y + x1 * y0 - y1 * x0)
                max_dev = max(max_dev, num / chord)
            ctrl_offset.append(min(max_dev / chord, 0.8))
            n = len(speeds)
            if n >= 10:
                cut = max(1, int(n * 0.8))
                early = statistics.mean(speeds[:cut]) or 1e-6
                late = statistics.mean(speeds[cut:]) or 1e-6
                approach_slowdown.append(late / early)
            end_t, end_x, end_y = stroke[-1]
            for ct, cx, cy in clicks:
                if 0 <= ct - end_t <= 400 and math.hypot(cx - end_x, cy - end_y) < 40:
                    pre_click_hover.append(ct - end_t)
                    break

    return {
        "version": 1,
        "source": sources,
        "path": {
            "dt_ms": band(dt_ms),
            "steps_per_100px": band(steps_per_100px),
            "ctrl_offset_frac": band(ctrl_offset),
            "speed_px_s": band(speed),
            "noise_px": {"p10": 0.2, "p50": 0.5, "p90": 1.2, "n": 0},
            "ease": "cosine",
        },
        "session": {
            "wander_count": [2, 3],
            "inter_stroke_pause_ms": band(pause_ms),
            "pre_click_hover_ms": band(pre_click_hover),
            "approach_slowdown": band(approach_slowdown),
            "max_moves_budget": 180,
            "micro_jitter_n": [3, 5],
        },
        "click": {
            "down_up_ms": band(click_dwell)
            if click_dwell
            else {"p10": 60.0, "p50": 90.0, "p90": 150.0, "n": 0},
        },
        "scroll": {"deltas": [70, 100, -40], "gap_ms": [120, 200]},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "data",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output JSON path (default: data/motion_profile.json)",
    )
    parser.add_argument(
        "--emit-python",
        action="store_true",
        help="Also print a MOTION_PROFILE dict snippet for embedding in bot.py",
    )
    args = parser.parse_args()

    sources: list[str] = []
    sessions: list[list[tuple[float, float, float, str]]] = []

    adserp = args.data_dir / "adserp_mouse" / "mouse-movement-data"
    if adserp.exists():
        sessions.extend(load_adserp_csvs(adserp))
        sources.append("adserp")

    psai = args.data_dir / "segments" / "psai"
    psai_sessions = load_psai_segments(psai)
    if psai_sessions:
        sessions.extend(psai_sessions)
        sources.append("psai")

    if not sessions:
        raise SystemExit(
            f"No sessions found under {args.data_dir}. Run ingest_adserp.py first."
        )

    profile = fit(sessions, sources or ["unknown"])
    out = args.out or (args.data_dir / "motion_profile.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(profile, indent=2) + "\n")
    print(f"Wrote {out} from sources={sources} sessions={len(sessions)}")
    if args.emit_python:
        print("MOTION_PROFILE = " + json.dumps(profile, indent=4))


if __name__ == "__main__":
    main()
