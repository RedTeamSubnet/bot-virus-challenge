#!/usr/bin/env python
# -*- coding: utf-8 -*-

## Standard libraries
import os
import sys
import logging
import subprocess

import requests

## Internal modules
from constants import Config
from driver import WebUIAutomate
logger = logging.getLogger(__name__)


def check_internet(url: str = "https://www.google.com", timeout: float = 3.0) -> bool:
    try:
        response = requests.head(url, timeout=timeout, allow_redirects=True)
        return response.ok
    except requests.RequestException as err:
        logger.warning(f"Internet check failed for {url}: {err}")
        return False


def main() -> None:
    logging.basicConfig(
        stream=sys.stdout,
        level=logging.INFO,
        datefmt="%Y-%m-%d %H:%M:%S %z",
        format="[%(asctime)s | %(levelname)s | %(filename)s:%(lineno)d]: %(message)s",
    )

    logger.info("Starting WebUI automation bot...")

    # Get web URL from environment variable
    try:
        container_config = Config()
        _web_url = container_config.WEB_URL
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        logger.info("Falling back to environment variables directly...")
        _web_url = os.getenv("BV_WEB_URL")

    if not _web_url:
        # Fallback: try to get host gateway IP
        _command = "ip route | awk '/default/ { print $3 }'"
        try:
            _host = subprocess.check_output(_command, shell=True, text=True).strip()
        except Exception:
            _host = "challenge-api"
        _web_url = f"http://{_host}:10001/_web"
        logger.warning(f"BV_WEB_URL not set, using fallback: {_web_url}")

    logger.info(f"Challenge web URL: {_web_url}")

    if check_internet():
        logger.info("Internet check: OK (https://www.google.com reachable)")
    else:
        logger.warning("Internet check: FAILED (no HTTPS egress from container)")

    # Get session count
    try:
        _session_count = container_config.SESSION_COUNT
    except Exception:
        _session_count = int(os.getenv("BV_SESSION_COUNT", "2"))

    logger.info(f"Running {_session_count} session(s)")

    for _ in range(int(_session_count) if _session_count else 2):
        _webui_automate = WebUIAutomate(web_url=_web_url)
        _webui_automate()

    logger.info("Done!\n")
    return


if __name__ == "__main__":
    main()
