# -*- coding: utf-8 -*-

import os
import shutil
import threading
from typing import Dict

import docker
import requests
from pydantic import validate_call

from api.config import config
from api.endpoints.challenge.docker_client import get_client
from api.logger import logger


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
    """The miner submission is exactly two files. They are written to the
    shared commit volume, never into the repo checkout, so a scoring run
    leaves the working tree clean."""
    bot_dir = _reset_build_context(config.challenge.commit_dir)
    with open(os.path.join(bot_dir, "bot.py"), "w") as f:
        f.write(bot_py)
    with open(os.path.join(bot_dir, "Dockerfile"), "w") as f:
        f.write(dockerfile)


@validate_call(config={"arbitrary_types_allowed": True})
def ensure_phase_network(
    docker_client: docker.DockerClient, network_name: str, internal: bool
) -> str:
    """Ensure the bot network exists on the nested daemon."""
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
def get_bot_network_gateway(
    docker_client: docker.DockerClient, network_name: str
) -> str:
    """Return the bridge gateway address reachable by bot containers."""
    network = docker_client.networks.get(network_name)
    for ipam_config in network.attrs.get("IPAM", {}).get("Config", []):
        gateway = ipam_config.get("Gateway")
        if gateway:
            return gateway
    raise ValueError(f"Could not determine gateway for bot network '{network_name}'")


# The bot container name is a fixed, shared slot on the nested daemon, and one
# bot is given 8g of memory, so only one may run at a time. This lock makes
# that serialization explicit in-process, which is what keeps two requests from
# force-removing each other's live container through remove_existing_container
# below. That helper stays as the recovery path for a slot left allocated by a
# run whose process died before its cleanup could run.
_BOT_CONTAINER_LOCK = threading.Lock()


def _remove_bot_container(container, container_name: str, run_ref: str) -> None:
    """Stop-if-running and remove, idempotently. force=True kills a running
    container before removing it, so this covers a hung bot and an exited one
    alike, and a container that is already gone counts as success."""
    logger.info(f"[{run_ref}] Cleaning up bot container: {container_name}")
    try:
        container.remove(force=True)
    except docker.errors.NotFound:
        logger.debug(f"[{run_ref}] Bot container already gone: {container_name}")
    except docker.errors.APIError as err:
        # Cleanup must never be the reason the request fails: the caller
        # already has a real result, and the next run's
        # remove_existing_container still reclaims the name.
        logger.warning(
            f"[{run_ref}] Could not remove bot container {container_name}: {err}"
        )
    else:
        logger.info(f"[{run_ref}] Bot container removed: {container_name}")


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

    run_ref = labels.get("score_job_id") or "no-job-id"
    container = None
    # Held across create -> wait -> cleanup so the shared container name has
    # exactly one owner at a time; see _BOT_CONTAINER_LOCK above. Acquired
    # outside the try so the finally only ever releases a lock it holds.
    _BOT_CONTAINER_LOCK.acquire()
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
        logger.info(
            f"[{run_ref}] Creating bot container: {container_name} from {image_tag}"
        )
        for attempt in range(3):
            try:
                container = docker_client.containers.run(image_tag, **run_kwargs)
                break
            except docker.errors.APIError as exc:
                # containers.run only binds `container` on success, so nothing
                # of ours exists to clean up here.
                if "already in use" not in str(exc).lower():
                    raise
                if attempt == 2:
                    raise
                remove_existing_container()
        if container is None:
            raise RuntimeError(f"Could not start bot container '{container_name}'")
        logger.info(
            f"[{run_ref}] Bot container started: {container_name} "
            f"({container.short_id})"
        )
        wait_timeout_sec = config.challenge.container_wait_timeout_sec or None
        try:
            wait_result = container.wait(timeout=wait_timeout_sec)
        except requests.exceptions.RequestException as err:
            # A read timeout and a dropped daemon connection both mean the exit
            # status is unknowable. Either way the finally below releases the
            # name instead of leaving it held forever.
            raise TimeoutError(
                f"Bot container '{container_name}' did not exit within "
                f"{wait_timeout_sec}s: {err}"
            ) from err
        exit_code = (
            int(wait_result.get("StatusCode", 1))
            if isinstance(wait_result, dict)
            else 0
        )
        logger.info(
            f"[{run_ref}] Bot container exited: {container_name} status={exit_code}"
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
        # The container is created with auto_remove=False, so this is the only
        # thing that frees the name. It must run on every path: success, bot
        # failure, wait timeout, and any exception in between. Removal comes
        # before the release so the next waiter finds the name free.
        if container is not None:
            _remove_bot_container(container, container_name, run_ref)
        _BOT_CONTAINER_LOCK.release()


def _base_bot_env(score_job_id: str) -> Dict[str, str]:
    return {
        "SCORE_JOB_ID": score_job_id,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPYCACHEPREFIX": "/tmp/pycache",
    }


def build_bot_image(bot_py: str, dockerfile: str, score_job_id: str = "") -> Dict:
    logger.info("Starting miner image build...")
    _write_build_context(bot_py=bot_py, dockerfile=dockerfile)
    docker_client = get_client()
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


def run_web_bot(session_count: int = 2, score_job_id: str = "") -> Dict:
    """Run the built miner image against the challenge's own /_web page."""
    docker_client = get_client()
    network_name = ensure_phase_network(
        docker_client,
        config.challenge.challenge_network_name,
        internal=True,
    )
    gateway = get_bot_network_gateway(docker_client, network_name)
    challenge_https_port = os.getenv("CHALLENGE_HTTPS_PROXY_PORT", "10443")
    # The bot sits on an internal network inside the nested daemon. The only
    # route back to this API is the sidecar's HTTPS proxy on the bridge
    # gateway, which forwards to the challenge container.
    challenge_base_url = f"https://{gateway}:{challenge_https_port}"
    challenge_web_url = f"{challenge_base_url}/_web"

    container_name = "bot_container"
    labels = {"type": "bot-executor-run"}
    if score_job_id:
        labels["score_job_id"] = score_job_id

    env = _base_bot_env(score_job_id)
    env.update(
        {
            "CHALLENGE_BASE_URL": challenge_base_url,
            "CHALLENGE_WEB_URL": challenge_web_url,
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
    "run_web_bot",
]
