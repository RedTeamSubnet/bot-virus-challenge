# -*- coding: utf-8 -*-

import time
from typing import TYPE_CHECKING, List

import vault_unlock

from api.config import config
from api.core import utils
from api.endpoints.challenge import runner
from api.endpoints.challenge.schemas import KeyPairPM
from api.helpers.crypto import asymmetric as asymmetric_helper
from api.logger import logger

if TYPE_CHECKING:
    from api.endpoints.challenge.payload_manager import PayloadManager


def gen_key_pairs() -> List[KeyPairPM]:
    pairs: List[KeyPairPM] = []
    for _ in range(config.challenge.n_run_per_ch):
        private_key, public_key = asymmetric_helper.gen_key_pair(
            key_size=config.api.security.asymmetric.key_size,
            as_str=True,
        )
        pairs.append(
            KeyPairPM(
                private_key=private_key,
                public_key=public_key,
                nonce=utils.gen_random_string(length=32),
            )
        )
    return pairs


def decrypt(ciphertext: str, private_key: str) -> str:
    return vault_unlock.decrypt_payload(
        encrypted_text=ciphertext,
        private_key_pem=private_key,
    )


def run_web_phase(
    manager: "PayloadManager", session_count: int, score_job_id: str
) -> bool:
    """Run the web phase and return whether the runner failed."""
    try:
        logger.info(f"Starting {session_count} bot session(s)")
        runner.run_web_bot(session_count=session_count, score_job_id=score_job_id)
    except Exception as err:
        logger.error(f"Runner failed: {err}; returning runner_fail_score")
        return True

    deadline = time.time() + config.challenge.bot_timeout
    while manager.completed_count() < session_count and time.time() < deadline:
        logger.info(
            f"Waiting... {manager.completed_count()}/{session_count} sessions recorded"
        )
        time.sleep(1)
    return False


__all__ = [
    "gen_key_pairs",
    "decrypt",
    "run_web_phase",
]
