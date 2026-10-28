"""Minimal reference bot for the v2 challenge page.

The page asks for four steps, in order:
  1. scroll the card list until the card showing a 6-character code appears,
  2. type that code into #bv-code-input,
  3. set the slider #bv-slider to the value in its "Set to N" label,
  4. click #verify-button.

This is a plain Selenium bot, not a stealth bot. It reads everything from the
page DOM.
"""

import re
from shutil import which

from selenium import webdriver
from selenium.webdriver import ActionChains
from selenium.webdriver.common.actions.wheel_input import ScrollOrigin
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait

SLIDER_PATTERN = re.compile(r"Set to\s+(\d+)", re.IGNORECASE)
SCROLL_STEP = 300
MAX_SCROLL_STEPS = 80

# The scroll step only counts once the code card is fully visible above the
# verification panel (same rule as the page: top >= 0, bottom <= panel top).
# textContent keeps the original case, so upper-cased labels (CSS
# text-transform) are not mistaken for the code.
CODE_CARD_JS = """
const card = document.getElementById('bv-code-card');
if (!card) return null;
const rect = card.getBoundingClientRect();
const panel = document.getElementById('bv-panel');
const limit = panel ? panel.getBoundingClientRect().top : window.innerHeight;
const match = /\\b[A-Z2-9]{6}\\b/.exec(card.textContent || '');
return {top: rect.top, bottom: rect.bottom, limit: limit,
        code: match ? match[0] : null};
"""


def scroll_until_code(driver) -> str:
    """Wheel-scroll until the code card sits fully above the panel."""
    origin = ScrollOrigin.from_viewport(640, 200)
    for _ in range(MAX_SCROLL_STEPS):
        card = driver.execute_script(CODE_CARD_JS)
        if card is None:
            raise RuntimeError("code card not found")
        if card["top"] >= 0 and card["bottom"] <= card["limit"] and card["code"]:
            return card["code"]
        if card["top"] < 0:
            delta = int(card["top"]) - 20  # overshot: scroll back up
        else:
            delta = min(SCROLL_STEP, int(card["bottom"] - card["limit"]) + 20)
        ActionChains(driver).scroll_from_origin(origin, 0, delta).pause(0.15).perform()
    raise RuntimeError("code card never fully visible")


def read_slider_target(driver) -> int:
    """Read N from the visible "Set to N" label."""
    text = driver.execute_script("return document.body.innerText || ''")
    match = SLIDER_PATTERN.search(text)
    if not match:
        raise RuntimeError("slider label not found")
    return int(match.group(1))


def set_slider(driver, slider, target: int) -> None:
    """Click the slider, jump to the start and step to the target value."""
    ActionChains(driver).move_to_element(slider).click().perform()
    slider.send_keys(Keys.HOME)
    slider.send_keys(Keys.ARROW_RIGHT * target)


def run_bot(url: str) -> bool:
    """Complete the challenge task once."""
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--window-size=1280,720")
    options.binary_location = which("chromium") or "/usr/bin/chromium"

    driver = None
    try:
        driver = webdriver.Chrome(
            service=Service(which("chromedriver") or "/usr/bin/chromedriver"),
            options=options,
        )
        driver.get(url)
        code_input = WebDriverWait(driver, 10).until(
            lambda browser: browser.find_element(By.ID, "bv-code-input")
        )

        code = scroll_until_code(driver)

        ActionChains(driver).move_to_element(code_input).pause(0.1).click().perform()
        code_input.send_keys(code)

        slider = driver.find_element(By.ID, "bv-slider")
        set_slider(driver, slider, read_slider_target(driver))

        button = driver.find_element(By.ID, "verify-button")
        ActionChains(driver).move_to_element(button).pause(0.1).click().perform()
        WebDriverWait(driver, 10).until(
            lambda browser: browser.execute_script(
                "return window.BV_SUBMITTED === true"
            )
        )
        return True
    except Exception:
        return False
    finally:
        if driver:
            driver.quit()
