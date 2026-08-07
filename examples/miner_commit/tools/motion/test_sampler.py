#!/usr/bin/env python3
"""Offline unit checks for AdSERP-fitted pointer sampling (no browser)."""

from __future__ import annotations

import math
import sys
import types
from pathlib import Path

# bot.py imports nodriver at module load; stub it for offline tests.
_nodriver = types.ModuleType("nodriver")
_cdp = types.ModuleType("nodriver.cdp")
_input = types.SimpleNamespace(
    dispatch_mouse_event=lambda *a, **k: None,
    MouseButton=lambda x: x,
)
_page = types.SimpleNamespace(
    enable=lambda: None,
    add_script_to_evaluate_on_new_document=lambda *_: None,
)
_cdp.input_ = _input
_cdp.page = _page
_nodriver.cdp = _cdp
_nodriver.start = None
sys.modules["nodriver"] = _nodriver
sys.modules["nodriver.cdp"] = _cdp

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "commit"))
import bot  # noqa: E402


def test_sample_band_within_bounds():
    band = {"p10": 10.0, "p50": 20.0, "p90": 40.0}
    for _ in range(200):
        v = bot._sample_band(band, lo=5.0, hi=50.0)
        assert 5.0 <= v <= 50.0


def test_bezier_ends_at_target():
    start, end = (10.0, 20.0), (400.0, 300.0)
    pts = bot._bezier_points(start, end, steps=24, ctrl_frac=0.1, noise_px=0.5, ease="cosine")
    assert len(pts) == 24
    assert math.hypot(pts[-1][0] - end[0], pts[-1][1] - end[1]) < 1e-6


def test_move_budget_cap():
    budget = bot.MoveBudget(30)
    got = budget.take(100)
    assert got == 30
    assert budget.left == 0
    assert budget.take(5) == 0
    assert budget.left == 0


def test_profile_has_required_keys():
    p = bot.MOTION_PROFILE
    assert p["path"]["dt_ms"]["p50"] > 0
    assert p["session"]["max_moves_budget"] <= 180
    assert p["click"]["down_up_ms"]["p50"] > 0


def test_bot_py_line_limit():
    lines = (ROOT / "src" / "commit" / "bot.py").read_text().splitlines()
    assert len(lines) <= 2000, f"bot.py has {len(lines)} lines"


if __name__ == "__main__":
    test_sample_band_within_bounds()
    test_bezier_ends_at_target()
    test_move_budget_cap()
    test_profile_has_required_keys()
    test_bot_py_line_limit()
    print("ok")
