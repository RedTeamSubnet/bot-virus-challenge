"""Minimal reference bot."""

from shutil import which

from selenium import webdriver
from selenium.webdriver import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait


def run_bot(url: str) -> bool:
    """Open the challenge page once."""
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
        button = WebDriverWait(driver, 10).until(
            lambda browser: browser.find_element(By.ID, "verify-button")
        )
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