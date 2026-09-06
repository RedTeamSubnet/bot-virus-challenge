# -*- coding: utf-8 -*-

import logging
import os
import sys
import time

import docker
from docker.errors import DockerException


# Deliberately the standard library logger, not api.logger: this module runs as
# a preflight from the entrypoint, before the app config is known to load, and
# api.logger imports api.config and opens log files as a side effect.
logger = logging.getLogger(__name__)

# A cold DinD data volume can leave the nested daemon starting for most of a
# minute before it answers anything.
_DEFAULT_TIMEOUT_SEC = 120.0
_DEFAULT_INTERVAL_SEC = 1.0

_client = None


def get_client() -> docker.DockerClient:
    """The shared daemon connection, built from DOCKER_HOST on first use."""
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client


def reset_client() -> None:
    """Drop the cached connection so the next call rebuilds it."""
    global _client
    _client = None


def _timeout_from_env() -> float:
    # Read straight from the environment rather than api.config, so a config
    # problem is never reported as a Docker problem.
    try:
        return float(os.getenv("BV_CHALLENGE_DOCKER_READY_TIMEOUT_SEC", ""))
    except ValueError:
        return _DEFAULT_TIMEOUT_SEC


def wait_for_daemon(
    timeout_sec: float | None = None,
    interval_sec: float = _DEFAULT_INTERVAL_SEC,
) -> None:
    """Block until the Docker daemon answers a real request, or raise.

    The socket file appears before the sidecar's nested dockerd is bound to it,
    so checking that the path exists proves nothing. Only a round trip does.
    """
    if timeout_sec is None:
        timeout_sec = _timeout_from_env()

    deadline = time.monotonic() + timeout_sec
    last_error: Exception | None = None
    attempts = 0
    while True:
        attempts += 1
        try:
            get_client().ping()
        except (DockerException, OSError) as err:
            last_error = err
            # A connection that failed must not be cached and reused.
            reset_client()
        else:
            logger.info("Docker daemon answered after %s attempt(s).", attempts)
            return

        remaining_sec = deadline - time.monotonic()
        if remaining_sec <= 0:
            break
        time.sleep(min(interval_sec, remaining_sec))

    host = os.getenv("DOCKER_HOST", "the default socket")
    raise TimeoutError(
        f"Docker daemon at {host} did not answer within "
        f"{timeout_sec:.0f}s after {attempts} attempt(s): {last_error}"
    )


def main() -> int:
    logging.basicConfig(
        stream=sys.stdout,
        level=logging.INFO,
        datefmt="%Y-%m-%d %H:%M:%S %z",
        format="[%(asctime)s | %(levelname)s | %(filename)s:%(lineno)d]: %(message)s",
    )
    try:
        wait_for_daemon()
    except TimeoutError as err:
        logger.error("%s", err)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["get_client", "reset_client", "wait_for_daemon"]
