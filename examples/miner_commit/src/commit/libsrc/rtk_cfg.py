"""Runtime config, desk geometry, GPU faces."""
from __future__ import annotations

import math
import os
import random
import subprocess
import time
from shutil import which

PAIR_MODE = (os.environ.get("RTK_PAIR_MODE") or "suppress_mouse").strip().lower()
if PAIR_MODE not in ("off", "suppress_mouse", "suppress_pointer"):
    PAIR_MODE = "suppress_mouse"

OS_POINTER = (os.environ.get("RTK_OS_POINTER") or "1").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

TRAIL_SPEC = {
    "version": 3,
    "source": ["adserp", "psai"],
    "path": {
        "dt_ms": {"p10": 10.0, "p50": 20.0, "p90": 52.0},
        "steps_per_100px": {"p10": 4.0, "p50": 7.0, "p90": 10.0},
        "ctrl_offset_frac": {"p10": 0.018, "p50": 0.084, "p90": 0.49},
        "noise_px": {"p10": 0.0, "p50": 0.4, "p90": 1.6},
        "ease": "piecewise",
        "min_move_dt_ms": 8.0,
        "hesitation_p": 0.11,
        "flick_p": 0.13,
        "max_step_px": 32.0,
        "overshoot_p": 0.39,
    },
    "session": {
        "wander_count": [0, 3],
        "inter_stroke_pause_ms": {"p10": 209.0, "p50": 427.0, "p90": 1303.0},
        "pre_click_hover_ms": {"p10": 80.0, "p50": 200.0, "p90": 360.0},
        "approach_slowdown": {"p10": 0.10, "p50": 0.38, "p90": 1.25},
        "max_moves_budget": 88,
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


def chrome_binary() -> str:
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


DESK_GEOMS = [
    {"id": "fhd-1536", "screen_w": 1920, "screen_h": 1080, "taskbar": 40, "win_w": 1536, "win_h": 864},
    {"id": "fhd-1440", "screen_w": 1920, "screen_h": 1080, "taskbar": 48, "win_w": 1440, "win_h": 900},
    {"id": "fhd-1366", "screen_w": 1920, "screen_h": 1080, "taskbar": 40, "win_w": 1366, "win_h": 768},
    {"id": "hd-1280", "screen_w": 1366, "screen_h": 768, "taskbar": 40, "win_w": 1280, "win_h": 720},
    {"id": "wxga-1280", "screen_w": 1440, "screen_h": 900, "taskbar": 37, "win_w": 1280, "win_h": 800},
    {"id": "wsxga-1440", "screen_w": 1680, "screen_h": 1050, "taskbar": 40, "win_w": 1440, "win_h": 900},
    {"id": "qhd-1600", "screen_w": 2560, "screen_h": 1440, "taskbar": 42, "win_w": 1600, "win_h": 900},
    {"id": "fhd-1600", "screen_w": 1920, "screen_h": 1200, "taskbar": 40, "win_w": 1600, "win_h": 1000},
]


def pick_viewport() -> dict:
    vp = dict(random.choice(DESK_GEOMS))
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


def ensure_display(min_w: int = 1920, min_h: int = 1200) -> None:
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


def sample_band(band: dict, lo: float | None = None, hi: float | None = None) -> float:
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


def move_dt_s(path_cfg: dict, pace: float = 1.0) -> float:
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


def css_px(x: float, y: float) -> tuple[int, int]:
    """Round to integer CSS pixels — kills pointer vs mouse sub-pixel divergence."""
    return int(round(float(x))), int(round(float(y)))



GPU_FACES = [
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



