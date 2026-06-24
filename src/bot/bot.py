#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Reference bot for the Bot Virus challenge — single-file, two-file contract.

The miner submission is exactly two files: this ``bot.py`` and a ``Dockerfile``.
This reference bot opens the challenge web page (``/_web``) with a headless
Chrome and waits for the browser-side SDK to collect the integrity signals and
POST the encrypted payload to ``/_eval``. It does NOT fill forms, move the
mouse, scroll, or submit anything from Python — submission must happen from the
browser context (``window.BV_SUBMITTED === true``).

Endpoint configuration is read from the environment provided by the runner:

    CHALLENGE_WEB_URL   e.g. http://challenge-api:10001/_web   (preferred)
    CHALLENGE_BASE_URL  e.g. http://challenge-api:10001        (web url derived)
    BV_SESSION_COUNT    number of sessions to run              (default: 1)

Only the Selenium Python client is required on top of the base image.
"""

import os
import sys
import logging
import subprocess
import tempfile
from urllib.parse import urlparse

from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


logger = logging.getLogger(__name__)

_VIEWPORT_WIDTH = 1440
_VIEWPORT_HEIGHT = 900
_DEFAULT_PORT = "10001"


def resolve_web_url() -> str:
    """Resolve the challenge ``/_web`` URL from the environment.

    Order of preference: CHALLENGE_WEB_URL, then CHALLENGE_BASE_URL + "/_web",
    then the container's default gateway host, then a sane default.
    """

    _web_url = os.getenv("CHALLENGE_WEB_URL")
    if _web_url:
        return _web_url

    _base_url = os.getenv("CHALLENGE_BASE_URL")
    if _base_url:
        return f"{_base_url.rstrip('/')}/_web"

    # Fallback: try to reach the host via the default gateway.
    try:
        _host = subprocess.check_output(
            "ip route | awk '/default/ { print $3 }'", shell=True, text=True
        ).strip()
    except Exception:
        _host = "challenge-api"

    _web_url = f"http://{_host}:{_DEFAULT_PORT}/_web"
    logger.warning(f"CHALLENGE_WEB_URL not set, using fallback: {_web_url}")
    return _web_url


def setup_driver(web_url: str) -> WebDriver:
    """Initialize headless Chrome and load the challenge page."""

    _options = webdriver.ChromeOptions()
    _options.add_argument("--headless")
    _options.add_argument("--no-sandbox")
    _options.add_argument("--disable-gpu")
    _options.add_argument("--ignore-certificate-errors")

    # Treat the (HTTP) challenge origin as secure so the SDK gets a secure
    # context and WebCrypto SubtleCrypto is available for payload encryption.
    # The flag takes an *origin* and only applies with a dedicated user-data-dir.
    _parsed = urlparse(web_url)
    _origin = f"{_parsed.scheme}://{_parsed.netloc}"
    _options.add_argument(f"--unsafely-treat-insecure-origin-as-secure={_origin}")
    _options.add_argument(f"--user-data-dir={tempfile.mkdtemp(prefix='bv-chrome-')}")
    _options.add_argument(f"--window-size={_VIEWPORT_WIDTH},{_VIEWPORT_HEIGHT}")

    driver = webdriver.Chrome(options=_options)
    driver.get(web_url)

    # Ensure the minimal verification page has loaded.
    WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.ID, "status")))
    return driver


def run_session(driver: WebDriver) -> bool:
    """Wait for the browser-side SDK to submit the payload to /_eval."""

    try:
        WebDriverWait(driver, 30).until(
            lambda d: d.execute_script("return window.BV_SUBMITTED === true;")
        )
        logger.info("Browser-side SDK submitted the payload to /_eval.")

        # Surface browser console logs for debugging (best effort).
        try:
            for entry in driver.get_log("browser"):
                logger.info(f"[console][{entry.get('level')}] {entry.get('message')}")
        except Exception as err:
            logger.warning(f"Could not retrieve browser console logs: {err}")

        return True
    except Exception as err:
        logger.error(f"Browser-side submission did not complete: {err}")
        return False


def automate(web_url: str) -> bool:
    """Run a single automation session against the challenge web page."""

    driver = None
    try:
        driver = setup_driver(web_url)
        return run_session(driver)
    except WebDriverException as err:
        logger.error(f"WebDriver setup failed: {err}")
        return False
    except Exception as err:
        logger.error(f"Automation failed: {err}")
        return False
    finally:
        if driver is not None:
            try:
                driver.delete_all_cookies()
                driver.execute_script("window.localStorage.clear();")
            except Exception:
                pass
            driver.quit()


def main() -> None:
    logging.basicConfig(
        stream=sys.stdout,
        level=logging.INFO,
        datefmt="%Y-%m-%d %H:%M:%S %z",
        format="[%(asctime)s | %(levelname)s | %(filename)s:%(lineno)d]: %(message)s",
    )

    logger.info("Starting WebUI automation bot...")

    web_url = resolve_web_url()
    logger.info(f"Challenge web URL: {web_url}")

    try:
        session_count = int(os.getenv("BV_SESSION_COUNT", "1"))
    except (TypeError, ValueError):
        session_count = 1
    session_count = max(1, session_count)

    logger.info(f"Running {session_count} session(s)")

    for index in range(session_count):
        logger.info(f"Session {index + 1}/{session_count}")
        automate(web_url)

    logger.info("Done!\n")


if __name__ == "__main__":
    main()
