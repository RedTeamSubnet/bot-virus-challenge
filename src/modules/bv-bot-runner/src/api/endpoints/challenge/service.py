# -*- coding: utf-8 -*-

import os
import shutil
import time
import uuid
from typing import Dict
from urllib.parse import quote

import docker
import requests
from pydantic import validate_call

from api.config import config
from api.logger import logger

# Clean build context for the miner submission. In the runner container this
# resolves to /app/rest.bv-vm-runner/bot. The miner submission is exactly two
# files: Dockerfile and bot.py.


def _decode_bytes(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.decode("utf-8", errors="replace")


def _log_multiline(prefix: str, output: str, level: str = "debug") -> None:
    if not output:
        return
    log_fn = getattr(logger, level)
    for line in output.splitlines():
        line = line.rstrip()
        if line:
            log_fn(f"{prefix}: {line}")


def _log_build_event(event: Dict) -> None:
    if "stream" in event:
        _log_multiline("Build", str(event["stream"]), "debug")
    if "status" in event:
        detail = event.get("progress") or event.get("id") or ""
        logger.debug(f"Build: {event['status']} {detail}".rstrip())
    if "aux" in event:
        logger.debug(f"Build aux: {event['aux']}")
    if "errorDetail" in event:
        logger.error(f"Build error detail: {event['errorDetail']}")
    elif "error" in event:
        logger.error(f"Build error: {event['error']}")


def _reset_build_context(commit_dir: str) -> str:
    bot_dir = os.path.join(commit_dir, "bot")
    if os.path.isdir(bot_dir):
        shutil.rmtree(bot_dir)
    os.makedirs(bot_dir, exist_ok=True)
    return bot_dir


def _write_build_context(bot_py: str, dockerfile: str) -> None:
    bot_dir = _reset_build_context(config.challenge.commit_dir)
    with open(os.path.join(bot_dir, "bot.py"), "w") as f:
        f.write(bot_py)
    with open(os.path.join(bot_dir, "Dockerfile"), "w") as f:
        f.write(dockerfile)


@validate_call(config={"arbitrary_types_allowed": True})
def ensure_phase_network(
    docker_client: docker.DockerClient, network_name: str, internal: bool
) -> str:
    """Ensure a phase-specific inner network exists in DinD."""
    try:
        docker_client.networks.get(network_name)
        logger.info(
            f"Bot network '{network_name}' already exists, using existing network"
        )
        return network_name
    except docker.errors.NotFound:
        logger.info(f"Bot network '{network_name}' not found, creating it...")

    try:
        docker_client.networks.create(
            name=network_name,
            driver="bridge",
            internal=internal,
            check_duplicate=True,
            labels={"type": "bot-executor", "phase": network_name},
        )
        logger.success(f"Bot network '{network_name}' created successfully")
    except docker.errors.APIError as err:
        if "already exists" not in str(err).lower():
            raise
        logger.info(
            f"Bot network '{network_name}' already exists (created concurrently)"
        )
    return network_name


@validate_call(config={"arbitrary_types_allowed": True})
@validate_call(config={"arbitrary_types_allowed": True})
def get_bot_network_gateway(
    docker_client: docker.DockerClient, network_name: str
) -> str:
    """Return the bridge gateway address reachable by bot containers."""
    network = docker_client.networks.get(network_name)
    for config in network.attrs.get("IPAM", {}).get("Config", []):
        gateway = config.get("Gateway")
        if gateway:
            return gateway
    raise ValueError(f"Could not determine gateway for bot network '{network_name}'")


def _run_miner_container(
    *,
    docker_client: docker.DockerClient,
    image_tag: str,
    container_name: str,
    labels: Dict[str, str],
    environment: Dict[str, str | int],
    network_name: str,
) -> str:
    def remove_existing_container() -> None:
        try:
            existing = docker_client.containers.get(container_name)
        except docker.errors.NotFound:
            return

        logger.warning(
            f"Container name is already allocated: {container_name}; "
            "waiting for the previous run"
        )
        try:
            container_run_timeout_sec = config.challenge.container_run_timeout_sec
            if existing.status == "running":
                try:
                    existing.wait(timeout=container_run_timeout_sec)
                except requests.exceptions.ReadTimeout:
                    logger.warning(
                        f"Previous bot container did not finish within "
                        f"{container_run_timeout_sec}s; removing it"
                    )
            existing.remove(force=True)
        except docker.errors.NotFound:
            pass

    container = None
    try:
        runner_path = os.path.abspath(
            os.path.join(config.challenge.commit_dir, "bot_runner.py")
        )
        logger.info(f"Runner path: {runner_path}")
        run_kwargs = {
            "name": container_name,
            "labels": labels,
            "environment": environment,
            "network": network_name,
            "tmpfs": {
                "/tmp": "size=512M,mode=1777",
                "/dev/shm": "size=2g",
                "/var/tmp": "size=256M,mode=1777",
            },
            "mem_limit": "8g",
            "memswap_limit": "8g",
            "cpu_quota": 100000,
            "pids_limit": 256,
            "cap_drop": ["NET_RAW", "NET_ADMIN"],
            "security_opt": ["no-new-privileges"],
            "detach": True,
            "auto_remove": False,
            "shm_size": "4g",
            "read_only": False,
            "entrypoint": ["python3"],
            "command": ["/app/bot_runner.py"],
            "volumes": {runner_path: {"bind": "/app/bot_runner.py", "mode": "ro"}},
        }
        remove_existing_container()
        for attempt in range(3):
            try:
                container = docker_client.containers.run(image_tag, **run_kwargs)
                break
            except docker.errors.APIError as exc:
                if container is not None:
                    try:
                        container.remove(force=True)
                        pass
                    except docker.errors.NotFound:
                        pass
                    container = None
                if "already in use" not in str(exc).lower():
                    raise
                if attempt == 2:
                    raise
                remove_existing_container()
        if container is None:
            raise RuntimeError(f"Could not start bot container '{container_name}'")
        wait_result = container.wait()
        exit_code = (
            int(wait_result.get("StatusCode", 1))
            if isinstance(wait_result, dict)
            else 0
        )
        try:
            bot_logs = _decode_bytes(container.logs(stdout=True, stderr=True))
        except docker.errors.NotFound:
            bot_logs = ""
        _log_multiline(f"Bot {container_name}", bot_logs, "debug")
        if exit_code != 0:
            _log_multiline(f"Bot {container_name} failed", bot_logs, "info")
            raise ValueError(
                f"Bot container '{container_name}' exited with status {exit_code}"
            )
        return bot_logs
    finally:
        if container is not None:
            logger.debug(f"Bot container finished: {container_name}")


def _base_bot_env(score_job_id: str) -> Dict[str, str]:
    return {
        "SCORE_JOB_ID": score_job_id,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPYCACHEPREFIX": "/tmp/pycache",
    }


def _poll_simple_bot_result(
    user_identifier: str, timeout_sec: int, result_url: str
) -> bool | None:
    deadline = time.time() + timeout_sec
    max_attempts = config.challenge.simple_bot_poll_max_attempts
    interval_sec = config.challenge.simple_bot_poll_interval_sec

    for attempt in range(max_attempts):
        remaining_sec = deadline - time.time()
        if remaining_sec <= 0:
            break
        response = requests.get(
            f"{result_url}/api/bot-status",
            params={"user_identifier": user_identifier},
            timeout=min(10, remaining_sec),
        )
        response.raise_for_status()
        data = response.json()
        if data.get("isBot") is not None:
            return data.get("isBot")
        if attempt + 1 < max_attempts:
            time.sleep(min(interval_sec, max(0, deadline - time.time())))
    return None


def build_bot_image(bot_py: str, dockerfile: str, score_job_id: str = "") -> Dict:
    logger.info("Starting miner image build...")
    _write_build_context(bot_py=bot_py, dockerfile=dockerfile)
    docker_client = docker.from_env()
    try:
        _, build_logs = docker_client.images.build(
            path=config.challenge.commit_dir,
            dockerfile="bot/Dockerfile",
            tag=config.challenge.miner_image_tag,
            rm=True,
        )
        for log in build_logs:
            _log_build_event(log)
        logger.success(
            f"Successfully built miner image: {config.challenge.miner_image_tag}"
        )
        return {
            "status": "success",
            "message": "Miner image built successfully",
            "image_tag": config.challenge.miner_image_tag,
            "score_job_id": score_job_id,
        }
    except docker.errors.BuildError as err:
        logger.error(f"Docker build error: {err}")
        for log in getattr(err, "build_log", []) or []:
            if isinstance(log, dict):
                _log_build_event(log)
            else:
                logger.error(f"Build: {log}")
        raise ValueError(f"Failed to build Docker image: {str(err)}")


def run_simple_bot(score_job_id: str = "", timeout_sec: int = 60) -> Dict:
    docker_client = docker.from_env()
    user_identifier = score_job_id or uuid.uuid4().hex
    public_url = str(config.challenge.simple_bot_url).rstrip("/")

    frontend_url = public_url
    backend_url = public_url
    simple_web_url = f"{frontend_url}?user_identifier={quote(user_identifier)}"
    network_name = ensure_phase_network(
        docker_client,
        config.challenge.simple_bot_network_name,
        internal=False,
    )
    container_name = "bot_container"
    labels = {"type": "bot-executor-simple"}
    if score_job_id:
        labels["score_job_id"] = score_job_id

    env = _base_bot_env(score_job_id)
    env.update(
        {
            "CHALLENGE_BASE_URL": frontend_url,
            "CHALLENGE_WEB_URL": simple_web_url,
            "SIMPLE_BOT_FRONTEND_URL": frontend_url,
            "SIMPLE_BOT_BACKEND_URL": backend_url,
            "SIMPLE_BOT_USER_IDENTIFIER": user_identifier,
            "BOT_FRONTEND_URL": frontend_url,
            "BOT_BACKEND_URL": backend_url,
        }
    )
    logger.info(f"Running miner image against simple bot page: {simple_web_url}")
    runner_error = ""
    try:
        _run_miner_container(
            docker_client=docker_client,
            image_tag=config.challenge.miner_image_tag,
            container_name=container_name,
            labels=labels,
            environment=env,
            network_name=network_name,
        )
    except Exception as err:
        runner_error = str(err)
        logger.warning(f"Simple bot miner container exited unsuccessfully: {err}")
    is_bot = _poll_simple_bot_result(
        user_identifier, timeout_sec=timeout_sec, result_url=public_url
    )
    passed = is_bot is False
    return {
        "status": "success",
        "passed": passed,
        "is_bot": is_bot,
        "user_identifier": user_identifier,
        "runner_error": runner_error,
    }


def run_web_bot(session_count: int = 2, score_job_id: str = "") -> Dict:
    docker_client = docker.from_env()
    network_name = ensure_phase_network(
        docker_client,
        config.challenge.challenge_network_name,
        internal=True,
    )
    gateway = get_bot_network_gateway(docker_client, network_name)
    challenge_https_port = os.getenv("CHALLENGE_HTTPS_PROXY_PORT", "10443")
    challenge_base_url = f"https://{gateway}:{challenge_https_port}"
    challenge_web_url = f"{challenge_base_url}/_web"
    simple_bot_frontend_url = str(config.challenge.simple_bot_url).rstrip("/")
    simple_bot_backend_url = f"{simple_bot_frontend_url}/api"

    container_name = "bot_container"
    labels = {"type": "bot-executor-run"}
    if score_job_id:
        labels["score_job_id"] = score_job_id

    env = _base_bot_env(score_job_id)
    env.update(
        {
            "CHALLENGE_BASE_URL": challenge_base_url,
            "CHALLENGE_WEB_URL": challenge_web_url,
            "SIMPLE_BOT_FRONTEND_URL": simple_bot_frontend_url,
            "SIMPLE_BOT_BACKEND_URL": simple_bot_backend_url,
            "BOT_FRONTEND_URL": simple_bot_frontend_url,
            "BOT_BACKEND_URL": simple_bot_backend_url,
            "BV_SESSION_COUNT": session_count,
        }
    )
    logger.info(f"Running miner image against challenge page: {challenge_web_url}")
    _run_miner_container(
        docker_client=docker_client,
        image_tag=config.challenge.miner_image_tag,
        container_name=container_name,
        labels=labels,
        environment=env,
        network_name=network_name,
    )
    return {
        "status": "success",
        "message": "Bot executed successfully",
        "sessions_completed": session_count,
    }


__all__ = [
    "ensure_phase_network",
    "get_bot_network_gateway",
    "build_bot_image",
    "run_simple_bot",
    "run_web_bot",
]
