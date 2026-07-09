# -*- coding: utf-8 -*-

from typing import Dict, List, Tuple

import requests
import vault_unlock
from pydantic import validate_call

from api.core import utils
from api.endpoints.challenge.schemas import KeyPairPM
from api.helpers.crypto import asymmetric as asymmetric_helper
from api.logger import logger


@validate_call
def gen_key_pairs(n_challenge: int, key_size: int) -> List[KeyPairPM]:
    _key_pairs: List[KeyPairPM] = []
    for _ in range(n_challenge):
        _key_pair: Tuple[str, str] = asymmetric_helper.gen_key_pair(
            key_size=key_size, as_str=True
        )
        _private_key, _public_key = _key_pair
        _nonce = utils.gen_random_string(length=32)
        _key_pairs.append(
            KeyPairPM(private_key=_private_key, public_key=_public_key, nonce=_nonce)
        )
    return _key_pairs


@validate_call
def decrypt(ciphertext: str, private_key: str) -> str:
    return vault_unlock.decrypt_payload(
        encrypted_text=ciphertext, private_key_pem=private_key
    )


def _post_vm_request(
    vm_endpoint: str,
    path: str,
    payload: Dict,
    timeout: int = 120,
    ssl_verify: bool = True,
) -> Dict:
    logger.info(f"Sending VM request to {vm_endpoint}{path}")
    try:
        _response = requests.post(
            f"{vm_endpoint}{path}",
            json=payload,
            timeout=timeout,
            verify=ssl_verify,
        )
        if _response.status_code != 200:
            logger.error(
                f"VM request failed with status {_response.status_code}: {_response.text}"
            )
            raise ValueError(
                f"VM request failed with status {_response.status_code}: {_response.text}"
            )
        logger.success("Successfully received response from VM")
        return _response.json()
    except requests.Timeout:
        logger.error(f"VM request timed out after {timeout} seconds")
        raise
    except requests.RequestException as err:
        logger.error(f"VM request failed: {str(err)}")
        raise
    except Exception as err:
        logger.error(f"Unexpected error during VM request: {str(err)}")
        raise


@validate_call
def send_build_request(
    vm_endpoint: str,
    bot_py: str,
    dockerfile: str,
    timeout: int = 120,
    ssl_verify: bool = True,
    score_job_id: str = "",
) -> Dict:
    return _post_vm_request(
        vm_endpoint=vm_endpoint,
        path="/build",
        payload={
            "bot_py": bot_py,
            "dockerfile": dockerfile,
            "score_job_id": score_job_id,
        },
        timeout=timeout,
        ssl_verify=ssl_verify,
    )


@validate_call
def send_run_simple_bot_request(
    vm_endpoint: str,
    timeout: int = 120,
    ssl_verify: bool = True,
    score_job_id: str = "",
) -> Dict:
    return _post_vm_request(
        vm_endpoint=vm_endpoint,
        path="/run-simple-bot",
        payload={"score_job_id": score_job_id, "timeout_sec": timeout},
        timeout=timeout,
        ssl_verify=ssl_verify,
    )


@validate_call
def send_run_web_request(
    vm_endpoint: str,
    session_count: int,
    timeout: int = 120,
    ssl_verify: bool = True,
    score_job_id: str = "",
) -> Dict:
    return _post_vm_request(
        vm_endpoint=vm_endpoint,
        path="/run-web",
        payload={"session_count": session_count, "score_job_id": score_job_id},
        timeout=timeout,
        ssl_verify=ssl_verify,
    )


@validate_call
def send_build_and_run_request(
    vm_endpoint: str,
    bot_py: str,
    dockerfile: str,
    session_count: int,
    timeout: int = 120,
    ssl_verify: bool = True,
    score_job_id: str = "",
) -> Dict:
    return _post_vm_request(
        vm_endpoint=vm_endpoint,
        path="/build_and_run",
        payload={
            "bot_py": bot_py,
            "dockerfile": dockerfile,
            "session_count": session_count,
            "score_job_id": score_job_id,
        },
        timeout=timeout,
        ssl_verify=ssl_verify,
    )


__all__ = [
    "gen_key_pairs",
    "decrypt",
    "send_build_request",
    "send_run_simple_bot_request",
    "send_run_web_request",
    "send_build_and_run_request",
]
