"""RedTeam bot_virus_v1 miner entrypoint."""

from __future__ import annotations

import asyncio
import contextlib
import os
import random
import sys
from typing import Any

import nodriver as uc
from nodriver import cdp

# Image-baked modules (Dockerfile materializes /app/lib).
sys.path.insert(0, os.environ.get("PYTHONPATH", "/app/lib"))

from rtk_cfg import (  # noqa: E402
    GPU_FACES,
    TRAIL_SPEC,
    chrome_binary,
    ensure_display,
    pick_viewport,
)
from rtk_session import drive_pointer_session, wait_submitted  # noqa: E402
from rtk_spoof import compose_page_cloak  # noqa: E402


async def _run_async(url: str) -> bool:
    browser: Any = None
    try:
        vp = pick_viewport()
        ensure_display(vp["win_w"], vp["win_h"])
        fp = random.choice(GPU_FACES)
        cloak = compose_page_cloak(fp, vp)
        print(
            f"[bot] face={fp['id']} desk={vp['id']} "
            f"win={vp['win_w']}x{vp['win_h']} "
            f"pair={os.environ.get('RTK_PAIR_MODE')} os_ptr={os.environ.get('RTK_OS_POINTER')}",
            flush=True,
        )
        args = [
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
            "--disable-smooth-scrolling",
        ]
        browser = await uc.start(
            headless=False,
            browser_executable_path=chrome_binary(),
            sandbox=False,
            lang="en-US",
            browser_args=args,
        )
        try:
            tab0 = browser.main_tab or (browser.tabs[0] if browser.tabs else None)
            if tab0 is not None:
                await tab0.send(cdp.page.enable())
                await tab0.send(cdp.page.add_script_to_evaluate_on_new_document(cloak))
        except (OSError, RuntimeError, AttributeError) as exc:
            print(f"[bot] cloak hook failed: {exc}", flush=True)

        tab = await browser.get(url)
        await asyncio.sleep(random.uniform(1.8, 2.6))
        try:
            hooked = await tab.evaluate(
                "!!document[Symbol.for('rtk.spoof.v2')]",
                return_by_value=True,
            )
            if hasattr(hooked, "value"):
                hooked = hooked.value
            if hooked is not True:
                await tab.evaluate(cloak, return_by_value=True)
        except (OSError, RuntimeError, AttributeError):
            with contextlib.suppress(OSError, RuntimeError, AttributeError):
                await tab.evaluate(cloak, return_by_value=True)

        button = await tab.select("#verify-button", timeout=20)
        if button is None:
            raise RuntimeError("verify-button not found")
        await asyncio.sleep(random.uniform(0.5, 1.0))
        await drive_pointer_session(tab, button, TRAIL_SPEC)
        if not await wait_submitted(tab):
            raise RuntimeError("BV_SUBMITTED never became true")
        await asyncio.sleep(random.uniform(2.0, 2.8))
        return True
    except (OSError, RuntimeError, AttributeError, TimeoutError, ValueError) as exc:
        print(f"[bot] run failed: {exc}", flush=True)
        return False
    finally:
        if browser is not None:
            with contextlib.suppress(OSError, RuntimeError, AttributeError):
                browser.stop()


def run_bot(url: str) -> bool:
    try:
        return asyncio.run(_run_async(url))
    except (OSError, RuntimeError, KeyboardInterrupt) as exc:
        print(f"[bot] run_bot failed: {exc}", flush=True)
        return False