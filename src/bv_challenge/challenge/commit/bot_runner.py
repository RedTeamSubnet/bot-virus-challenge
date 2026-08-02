"""Platform-owned entrypoint for miner web bots."""

import logging
import os
import sys

from bot.bot import run_bot


logger = logging.getLogger(__name__)


def _session_count() -> int:
    """Return the platform-requested number of bot sessions."""
    try:
        return max(1, int(os.getenv("BV_SESSION_COUNT", "1")))
    except (TypeError, ValueError):
        return 1


def main() -> None:
    logging.basicConfig(
        stream=sys.stdout,
        level=logging.INFO,
        datefmt="%Y-%m-%d %H:%M:%S %z",
        format="[%(asctime)s | %(levelname)s | %(filename)s:%(lineno)d]: %(message)s",
    )
    web_url = os.getenv("CHALLENGE_WEB_URL")
    if not web_url:
        logger.error("CHALLENGE_WEB_URL is required")
        raise SystemExit(1)

    session_count = _session_count()
    logger.info("Running %s session(s) against %s", session_count, web_url)
    successful_sessions = 0
    for index in range(session_count):
        logger.info("Session %s/%s", index + 1, session_count)
        if run_bot(web_url):
            successful_sessions += 1

    if successful_sessions != session_count:
        logger.error(
            "Completed %s/%s requested session(s).",
            successful_sessions,
            session_count,
        )
        raise SystemExit(1)

    logger.info("Done!")


if __name__ == "__main__":
    main()
