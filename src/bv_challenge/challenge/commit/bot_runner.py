"""Platform-owned entrypoint for miner web bots."""

import logging
import os
import sys

from bot.bot import run_bot


logger = logging.getLogger(__name__)


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

    session_id = os.getenv("SESSION_ID", "")
    logger.info("[%s] running against %s", session_id or "no-session", web_url)
    if not run_bot(web_url):
        logger.error("[%s] bot run failed", session_id or "no-session")
        raise SystemExit(1)

    logger.info("[%s] done", session_id or "no-session")


if __name__ == "__main__":
    main()
