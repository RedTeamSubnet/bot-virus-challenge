"""Simple Selenium bot for the Bot Virus challenge page.

Steps, in order:
1. Click click-here-1, click-here-2, click-here-3.
2. Type a 16+ character username and password.
3. Click submit.
"""

from __future__ import annotations

import logging

from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as ec
from selenium.webdriver.support.ui import WebDriverWait

logger = logging.getLogger(__name__)

CHROMIUM_BIN = "/usr/bin/chromium"
CHROMEDRIVER_BIN = "/usr/bin/chromedriver"
TIMEOUT_SEC = 10
FILL_TEXT = "bot_fill_text_0000"  # 18 chars, meets the 16-char minimum


def _make_driver() -> webdriver.Chrome:
    options = webdriver.ChromeOptions()
    options.binary_location = CHROMIUM_BIN
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1440,900")
    return webdriver.Chrome(service=Service(CHROMEDRIVER_BIN), options=options)


def _click(driver: webdriver.Chrome, element_id: str) -> None:
    element = WebDriverWait(driver, TIMEOUT_SEC).until(
        ec.element_to_be_clickable((By.ID, element_id))
    )
    element.click()


def _type(driver: webdriver.Chrome, element_id: str, text: str) -> None:
    element = WebDriverWait(driver, TIMEOUT_SEC).until(
        ec.element_to_be_clickable((By.ID, element_id))
    )
    element.send_keys(text)


def run_bot(url: str) -> bool:
    """Open the challenge page and perform the required actions once."""
    if not url:
        return False

    driver = None
    try:
        driver = _make_driver()
        driver.get(url)

        for corner in ("click-here-1", "click-here-2", "click-here-3"):
            _click(driver, corner)

        _type(driver, "username", FILL_TEXT)
        _type(driver, "password", FILL_TEXT)

        _click(driver, "submit")
    except WebDriverException as exc:
        logger.warning("browser run failed: %s", exc)
        return False
    finally:
        if driver is not None:
            driver.quit()

    return True
