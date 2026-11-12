import os
import logging

import pytest

# Required, env-only secret (no yaml default). Provide a test value before the
# app imports and loads config. Real deployments set this to match
# MDM_CHALLENGE_API_KEY on the bot-runner VM.
os.environ.setdefault("BV_CHALLENGE_VM_API_KEY", "test-vm-api-key-123")

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session", autouse=True)
def setup_and_teardown():
    # Equivalent of setUp
    logger.info("Setting up...")

    yield  # This is where the testing happens!

    # Equivalent of tearDown
    logger.info("Tearing down!")
