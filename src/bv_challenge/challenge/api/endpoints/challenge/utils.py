# -*- coding: utf-8 -*-

from typing import Any, Dict, List

import requests

from api.config import config
from api.logger import logger


def _post_vm_request(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    vm_endpoint = str(config.challenge.vm_endpoint).rstrip("/")
    timeout = config.challenge.vm_timeout
    logger.info(f"Sending VM request to {vm_endpoint}{path}")
    try:
        response = requests.post(
            f"{vm_endpoint}{path}",
            json=payload,
            timeout=timeout,
            verify=config.challenge.vm_ssl_verify,
            headers={"X-API-Key": config.challenge.vm_api_key.get_secret_value()},
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


def send_build_request(bot_py: str, dockerfile: str, session_id: str) -> Dict[str, Any]:
    return _post_vm_request(
        "/build",
        {
            "bot_py": bot_py,
            "dockerfile": dockerfile,
            "session_id": session_id,
        },
    )


def send_run_web_request(session_id: str, keep_image: bool = False) -> Dict[str, Any]:
    # keep_image: the runner keeps the built miner image for the next run of
    # the same submission (runner >= keep_image support; older runners ignore
    # the field and rebuild nothing, so the next run fails loudly as "run").
    return _post_vm_request(
        "/run-web", {"session_id": session_id, "keep_image": keep_image}
    )


def compare_runs(session_ids: List[str]) -> Dict[str, Any]:
    """Ask the scorer to judge the scored sessions of one submission together.

    Returns the scorer's verdict: {"runs", "missing", "level", "score_cap"}.
    Raises on any transport, status or shape problem: a comparison that could
    not be made is an infrastructure failure, never a verdict.
    """
    endpoint = str(config.challenge.scorer_endpoint).rstrip("/")
    response = requests.post(
        f"{endpoint}/api/v1/score/runs",
        json={"session_ids": session_ids},
        timeout=config.challenge.scorer_timeout,
        verify=config.challenge.scorer_ssl_verify,
        headers={"X-API-Key": config.challenge.api_key.get_secret_value()},
    )
    if response.status_code != 200:
        raise ValueError(f"scorer run comparison returned {response.status_code}")
    data = response.json().get("data")
    if not isinstance(data, dict) or not isinstance(data.get("level"), str):
        raise ValueError("scorer run comparison returned no verdict")
    cap = data.get("score_cap")
    if cap is not None and (
        isinstance(cap, bool)
        or not isinstance(cap, (int, float))
        or not 0.0 <= cap <= 1.0
    ):
        raise ValueError("scorer run comparison returned an invalid cap")
    if data.get("runs") != len(session_ids) or data.get("missing") != 0:
        raise ValueError(
            f"scorer compared {data.get('runs')} of {len(session_ids)} sessions "
            f"({data.get('missing')} missing)"
        )
    return data


__all__ = [
    "compare_runs",
    "send_build_request",
    "send_run_web_request",
]
