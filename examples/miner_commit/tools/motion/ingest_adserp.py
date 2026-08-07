#!/usr/bin/env python3
"""Download + normalize AdSERP mouse trajectories to a shared segment schema."""

from __future__ import annotations

import argparse
import csv
import json
import zipfile
from pathlib import Path
from urllib.request import urlretrieve

ZENODO_MOUSE_ZIP = (
    "https://zenodo.org/records/15236546/files/mouse-movement-data.zip?download=1"
)
MOVE_EVENTS = {"mousemove"}
CLICK_EVENTS = {"click", "mousedown"}


def download_zip(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        print(f"Downloading {url}")
        urlretrieve(url, dest)
    return dest


def normalize_csv(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                t = float(row["timestamp"])
                x = float(row["xpos"])
                y = float(row["ypos"])
            except (KeyError, TypeError, ValueError):
                continue
            event = row.get("event", "")
            if event in MOVE_EVENTS:
                kind = "move"
            elif event in CLICK_EVENTS:
                kind = "down" if event == "mousedown" else "click"
            elif event == "mouseup":
                kind = "up"
            elif event in {"wheel", "scroll"}:
                kind = "wheel"
            else:
                continue
            rows.append({"t": t, "x": x, "y": y, "type": kind})
    if not rows:
        return []
    t0 = rows[0]["t"]
    for row in rows:
        row["t"] = row["t"] - t0
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "data",
    )
    parser.add_argument("--limit", type=int, default=0, help="Max CSV files (0=all)")
    args = parser.parse_args()

    zip_path = args.data_dir / "mouse-movement-data.zip"
    download_zip(ZENODO_MOUSE_ZIP, zip_path)
    extract_dir = args.data_dir / "adserp_mouse"
    if not (extract_dir / "mouse-movement-data").exists():
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_dir)

    csv_files = sorted((extract_dir / "mouse-movement-data").glob("*.csv"))
    if args.limit:
        csv_files = csv_files[: args.limit]

    out_dir = args.data_dir / "segments" / "adserp"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for csv_path in csv_files:
        events = normalize_csv(csv_path)
        if len(events) < 8:
            continue
        out = out_dir / f"{csv_path.stem}.json"
        out.write_text(json.dumps({"source": "adserp", "file": csv_path.name, "events": events}))
        written += 1
    print(f"Wrote {written} normalized segments to {out_dir}")


if __name__ == "__main__":
    main()
