# -*- coding: utf-8 -*-

from typing import Any, Dict

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


def send_build_request(
    bot_py: str, dockerfile: str, session_id: str
) -> Dict[str, Any]:
    return _post_vm_request(
        "/build",
        {
            "bot_py": bot_py,
            "dockerfile": dockerfile,
            "session_id": session_id,
        },
    )


def send_run_web_request(session_id: str) -> Dict[str, Any]:
    return _post_vm_request("/run-web", {"session_id": session_id})


__all__ = [
    "send_build_request",
    "send_run_web_request",
]
