import logging
import time
import json

from selenium.webdriver.common.by import By
from selenium.webdriver.common.actions.action_builder import ActionBuilder
from selenium.webdriver.common.actions.pointer_input import PointerInput

from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

logger = logging.getLogger(__name__)


def run_bot(
    driver: WebDriver,
) -> bool:
    """Run bot to automate login.

    Args:
        driver (WebDriver, required): Selenium WebDriver instance.

    Returns:
        bool: True if login is successful, False otherwise.
    """

    try:
        _wait = WebDriverWait(driver, 15)

        mouse = PointerInput(kind="mouse", name="mouse")

        # Execute JavaScript to get ACTIONS_LIST
        actions_list = json.loads(driver.execute_script("return window.ACTIONS_LIST;"))
        if actions_list:
            logger.info("Retrieved config from window.ACTIONS_LIST")
        else:
            logger.error("window.ACTIONS_LIST is empty or doesn't exist")
            return False

        # Handle nested list structure - if actions_list is a list of lists, flatten it
        if isinstance(actions_list, list) and len(actions_list) > 0 and isinstance(actions_list[0], list):
            logger.info("Detected nested actions_list structure, flattening...")
            actions_list = actions_list[0]

        # Perform configured actions
        for i, _action in enumerate(actions_list):
            if _action["type"] == "click":
                x = _action["args"]["location"]["x"]
                y = _action["args"]["location"]["y"]

                logger.info(f"Action {i+1}: Clicking at ({x}, {y})")

                try:
                    actions = ActionBuilder(driver, mouse=mouse)
                    actions.pointer_action.move_to_location(x, y)
                    actions.pointer_action.click()
                    actions.perform()

                    time.sleep(0.5)

                except Exception as e:
                    logger.error(f"Failed to perform action {i+1}: {e}")
                    continue

            if _action["type"] == "input":
                _selector = _action["selector"]
                _args = _action["args"]

                logger.info(
                    f"Action {i+1}: Inputting '{_args['text']}' to '{_selector}'"
                )

                try:
                    _element = driver.find_element(By.ID, _selector["id"])
                    _element.clear()
                    _element.send_keys(_args["text"])
                except Exception as e:
                    logger.error(f"Failed to perform action {i+1}: {e}")
                    continue

        # Click login button without scrolling
        _login_button = _wait.until(
            EC.presence_of_element_located((By.ID, "login-button"))
        )
        _login_button.click()

        logger.info("Scrolling to find end-session button")

        lenOfPage = driver.execute_script(
            "window.scrollTo(0, document.body.scrollHeight);var lenOfPage=document.body.scrollHeight;return lenOfPage;"
        )
        logger.info(f"Initial page length: {lenOfPage}")
        match = False
        while match == False:
            lastCount = lenOfPage
            time.sleep(3)
            lenOfPage = driver.execute_script(
                "window.scrollTo(0, document.body.scrollHeight);var lenOfPage=document.body.scrollHeight;return lenOfPage;"
            )
            if lastCount == lenOfPage:
                logger.info("Reached bottom of page")
                match = True

        _end_session_button = _wait.until(
            EC.presence_of_element_located((By.CLASS_NAME, "end-session"))
        )

        driver.execute_script(
            "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});",
            _end_session_button,
        )
        time.sleep(1)  # Give time for smooth scrolling to complete

        _end_session_button.click()
        logger.info("Clicked end-session button")

        # Give the browser a moment to flush console messages
        time.sleep(1)

        # Attempt to read browser console logs (may not be supported by all drivers)
        try:
            console_logs = driver.get_log("browser")
            if console_logs:
                logger.info("Browser console logs:")
                for entry in console_logs:
                    level = entry.get("level")
                    msg = entry.get("message")
                    source = entry.get("source") or entry.get("sourceURL") or ""
                    ts = entry.get("timestamp")
                    logger.info(f"[console][{level}] {ts} {source} - {msg}")
            else:
                logger.info("No browser console logs available.")
        except Exception as e:
            logger.warning(f"Could not retrieve browser console logs: {e}")

        return True

    except Exception as err:
        logger.error(f"Login failed: {err}")
        return False
