# -*- coding: utf-8 -*-

import time
from typing import TYPE_CHECKING, Any, Dict, List

import requests
import vault_unlock

from api.config import config
from api.core import utils
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


def _post_vm_request(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    vm_endpoint = config.challenge.vm_endpoint.rstrip("/")
    timeout = config.challenge.vm_timeout
    logger.info(f"Sending VM request to {vm_endpoint}{path}")
    try:
        response = requests.post(
            f"{vm_endpoint}{path}",
            json=payload,
            timeout=timeout,
            verify=config.challenge.vm_ssl_verify,
        )
        if response.status_code != 200:
            raise ValueError(
                f"VM request failed with status {response.status_code}: {response.text}"
            )
        logger.success("Successfully received response from VM")
        return response.json()
    except requests.Timeout:
        logger.error(f"VM request timed out after {timeout} seconds")
        raise
    except requests.RequestException as err:
        logger.error(f"VM request failed: {err}")
        raise


def send_build_request(
    bot_py: str, dockerfile: str, score_job_id: str
) -> Dict[str, Any]:
    return _post_vm_request(
        "/build",
        {
            "bot_py": bot_py,
            "dockerfile": dockerfile,
            "score_job_id": score_job_id,
        },
    )


def send_run_simple_bot_request(score_job_id: str) -> Dict[str, Any]:
    return _post_vm_request(
        "/run-simple-bot",
        {
            "score_job_id": score_job_id,
            "timeout_sec": config.challenge.vm_timeout,
        },
    )


def send_run_web_request(session_count: int, score_job_id: str) -> Dict[str, Any]:
    return _post_vm_request(
        "/run-web",
        {
            "session_count": session_count,
            "score_job_id": score_job_id,
        },
    )


def run_web_phase(
    manager: "PayloadManager", session_count: int, score_job_id: str
) -> bool:
    """Run the web phase and return whether the runner failed."""
    try:
        logger.info(f"Starting {session_count} bot session(s) via runner")
        send_run_web_request(session_count=session_count, score_job_id=score_job_id)
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
    "send_build_request",
    "send_run_simple_bot_request",
    "send_run_web_request",
    "run_web_phase",
]
