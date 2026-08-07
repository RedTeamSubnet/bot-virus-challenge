#!/usr/bin/env python3
"""Normalize PSAI browser-task events into the shared segment schema.

Requires: pip install datasets
Downloads only the parquet metadata (~8GB), not videos/DOMs.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_events(raw) -> list[dict]:
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
    else:
        data = raw
    if not isinstance(data, list):
        return []

    rows: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action", "")).lower()
        if action in {"move", "mousemove", "pointermove"}:
            kind = "move"
        elif action in {"click", "mousedown", "mouseup", "down", "up"}:
            if "up" in action:
                kind = "up"
            elif "down" in action:
                kind = "down"
            else:
                kind = "click"
        elif action in {"wheel", "scroll", "mousewheel"}:
            kind = "wheel"
        else:
            continue
        try:
            t = float(item.get("time_stamp", item.get("timestamp", item.get("t"))))
            x = float(item.get("x", item.get("xpos")))
            y = float(item.get("y", item.get("ypos")))
        except (TypeError, ValueError):
            continue
        rows.append({"t": t, "x": x, "y": y, "type": kind})
    if not rows:
        return []
    t0 = rows[0]["t"]
    for row in rows:
        row["t"] = row["t"] - t0
    # PSAI timestamps are often fractional seconds; AdSERP uses epoch ms.
    # Convert to milliseconds when inter-event gaps look like seconds.
    if len(rows) >= 3:
        gaps = [
            rows[i]["t"] - rows[i - 1]["t"]
            for i in range(1, min(len(rows), 50))
            if rows[i]["t"] > rows[i - 1]["t"]
        ]
        if gaps:
            mid = sorted(gaps)[len(gaps) // 2]
            if mid < 5.0:
                for row in rows:
                    row["t"] = row["t"] * 1000.0
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "data",
    )
    parser.add_argument("--limit", type=int, default=500, help="Max browser tasks")
    parser.add_argument(
        "--streaming",
        action="store_true",
        help="Use datasets streaming mode (lower disk, slower)",
    )
    args = parser.parse_args()

    try:
        from datasets import load_dataset
    except ImportError as exc:
        raise SystemExit("Install datasets first: pip install datasets") from exc

    ds = load_dataset(
        "anaisleila/computer-use-data-psai",
        split="train",
        streaming=args.streaming,
    )
    # Skip screenshots/video columns — we only need interaction events.
    keep = ["unique_data_id", "task_name", "category", "events"]
    available = [c for c in keep if c in ds.column_names]
    ds = ds.select_columns(available)

    out_dir = args.data_dir / "segments" / "psai"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for row in ds:
        if row.get("category") != "BROWSER_TASK":
            continue
        events = parse_events(row.get("events"))
        if len(events) < 8:
            continue
        uid = row.get("unique_data_id") or f"psai_{written}"
        out = out_dir / f"{uid}.json"
        out.write_text(
            json.dumps(
                {
                    "source": "psai",
                    "file": uid,
                    "task_name": row.get("task_name"),
                    "events": events,
                }
            )
        )
        written += 1
        if written >= args.limit:
            break
    print(f"Wrote {written} PSAI browser segments to {out_dir}")


if __name__ == "__main__":
    main()
