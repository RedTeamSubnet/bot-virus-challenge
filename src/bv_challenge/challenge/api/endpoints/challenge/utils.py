# -*- coding: utf-8 -*-

import os
import re
import time
import shutil
import random
import requests
import subprocess
from datetime import datetime, timezone
from typing import List, Dict, Union, Tuple, Optional

import vault_unlock
from api.config import config
import docker
from docker.models.networks import Network
from docker import DockerClient
from pydantic import validate_call

from api.core.constants import ErrorCodeEnum, ENV_PREFIX
from api.core import utils
from api.core.exceptions import BaseHTTPException
from api.helpers.crypto import asymmetric as asymmetric_helper
from api.endpoints.challenge.schemas import KeyPairPM, MinerOutput
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
        _key_pair_pm = KeyPairPM(
            private_key=_private_key, public_key=_public_key, nonce=_nonce
        )
        _key_pairs.append(_key_pair_pm)

    return _key_pairs

@validate_call
def gen_cb_actions(
    n_challenge: int = 10,
    window_width: int = 1420,
    window_height: int = 740,
    n_checkboxes: int = 5,
    min_distance: int = 300,
    max_factor: int = 10,
    checkbox_size: int = 20,  # Assuming checkbox size ~20px
    exclude_areas: Union[List[Dict[str, int]], None] = None,
    pre_action_list: Union[
        List[Dict[str, Union[int, str, Dict[str, Dict[str, int]]]]], None
    ] = None,
) -> List[Dict[str, Union[int, str, Dict[str, Dict[str, int]]]]]:

    _max_attempts = n_checkboxes * max_factor  # Avoid infinite loops

    _challenge_list = []
    for _ in range(n_challenge):
        _n_attempts = 0
        _i = 0
        _action_list = []

        if pre_action_list:
            _action_list = pre_action_list
        while len(_action_list) < n_checkboxes:
            _x = random.randint(checkbox_size, window_width - checkbox_size)
            _y = random.randint(checkbox_size, window_height - checkbox_size)

            _is_near = False
            _i = len(_action_list)
            for _action in _action_list:
                if _action["type"] == "click":
                    ## Calculate distance between two points using Euclidean distance:
                    if (_x - _action["args"]["location"]["x"]) ** 2 + (
                        _y - _action["args"]["location"]["y"]
                    ) ** 2 < min_distance**2:
                        _is_near = True
                        break

            _is_in_area = False
            if exclude_areas:
                for _area in exclude_areas:
                    if (_area["x1"] <= _x <= _area["x2"]) and (
                        _area["y1"] <= _y <= _area["y2"]
                    ):
                        _is_in_area = True
                        break

            if (not _is_near) and (not _is_in_area):
                _action = {
                    "id": _i,
                    "type": "click",
                    "args": {"location": {"x": _x, "y": _y}},
                }
                _action_list.append(_action)

            _n_attempts += 1

            if _max_attempts <= _n_attempts:
                logger.warning("Skipped generating positions due to max attempts!")
                break
        _next_id = len(_action_list)
        _action_list.extend(
            [
                {
                    "id": _next_id,
                    "type": "input",
                    "selector": {
                        "name": "username",
                        "id": utils.gen_random_string(length=32),
                    },
                    "args": {"text": utils.gen_random_string(length=32)},
                },
                {
                    "id": _next_id + 1,
                    "type": "input",
                    "selector": {
                        "name": "password",
                        "id": utils.gen_random_string(length=32),
                    },
                    "args": {"text": utils.gen_random_string(length=32)},
                },
            ]
        )
        _challenge_list.append(_action_list)

    return _challenge_list

@validate_call
def decrypt(ciphertext: str, private_key: str) -> str:

    _plaintext: str = vault_unlock.decrypt_payload(
        encrypted_text=ciphertext, private_key_pem=private_key
    )
    return _plaintext


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
    """
    Send build and run request to external VM.

    Args:
        vm_endpoint: VM endpoint URL
        bot_py: Bot Python code
        dockerfile: Dockerfile content
        session_count: Number of sessions to run
        timeout: Request timeout in seconds
        ssl_verify: Whether to verify SSL certificates

    Returns:
        Response from VM containing session results

    Raises:
        requests.RequestException: If request fails
        ValueError: If response is invalid
    """
    logger.info(f"Sending build and run request to VM: {vm_endpoint}")

    try:
        _payload = {
            "bot_py": bot_py,
            "dockerfile": dockerfile,
            "session_count": session_count,
            "score_job_id": score_job_id,
        }

        _response = requests.post(
            f"{vm_endpoint}/build_and_run",
            json=_payload,
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

        _result = _response.json()
        logger.success("Successfully received response from VM")
        return _result

    except requests.Timeout:
        logger.error(f"VM request timed out after {timeout} seconds")
        raise
    except requests.RequestException as err:
        logger.error(f"VM request failed: {str(err)}")
        raise
    except Exception as err:
        logger.error(f"Unexpected error during VM request: {str(err)}")
        raise


__all__ = [
    "gen_key_pairs",
    "decrypt",
    "send_build_and_run_request",
]
