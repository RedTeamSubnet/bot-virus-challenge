# -*- coding: utf-8 -*-

import hashlib
import pathlib
import threading
import uuid
from typing import Dict, Union

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import validate_call

from api.config import config
from api.core.constants import ErrorCodeEnum
from api.core.exceptions import BaseHTTPException
from api.endpoints.challenge import scoring
from api.endpoints.challenge import utils as challenge_utils
from api.endpoints.challenge.payload_manager import PayloadManager
from api.endpoints.challenge.schemas import MinerInput, MinerOutput
from api.logger import logger

_SRC_DIR = pathlib.Path(__file__).parent.parent.parent.parent.resolve()
payload_manager = PayloadManager()
_latest_result_lock = threading.Lock()
_latest_result: Dict[str, Union[float, str, bool, None]] = {
    "score": None,
    "feedback": "",
    "phase": "not_started",
    "simple_bot_passed": None,
}
SCHEMA_VERSION = "bv-runtime-1"


def _set_latest_result(
    *,
    score: float,
    feedback: str,
    phase: str,
    simple_bot_passed: bool | None,
) -> None:
    with _latest_result_lock:
        _latest_result.update(
            {
                "score": score,
                "feedback": feedback,
                "phase": phase,
                "simple_bot_passed": simple_bot_passed,
            }
        )


def get_result() -> Dict[str, Union[float, str, bool, None]]:
    with _latest_result_lock:
        return dict(_latest_result)


def get_task() -> MinerInput:
    return MinerInput()


@validate_call
def score(miner_output: MinerOutput) -> float:
    """Build, verify, and score one miner submission."""
    expected_sessions = config.challenge.n_run_per_ch
    required_tasks = config.challenge.n_ch_per_epoch * expected_sessions
    score_job_id = uuid.uuid4().hex
    bot_py = miner_output.get_file("bot.py")
    dockerfile = miner_output.get_file("Dockerfile")

    with payload_manager.run_lock:
        try:
            challenge_utils.send_build_request(bot_py, dockerfile, score_job_id)
        except Exception as err:
            logger.error(f"Failed to build miner container: {err}")
            _set_latest_result(
                score=0.0,
                feedback="failed to build miner container",
                phase="build",
                simple_bot_passed=None,
            )
            return 0.0

        try:
            simple_result = challenge_utils.send_run_simple_bot_request(score_job_id)
        except Exception as err:
            logger.error(f"Simple bot detection phase failed: {err}")
            _set_latest_result(
                score=0.0,
                feedback="failed in simple bot detection phase",
                phase="simple_bot",
                simple_bot_passed=False,
            )
            return 0.0

        if simple_result.get("passed") is not True:
            logger.info(f"Simple bot detection rejected miner: {simple_result}")
            _set_latest_result(
                score=0.0,
                feedback="failed in simple bot detection phase",
                phase="simple_bot",
                simple_bot_passed=False,
            )
            return 0.0

        if (
            not payload_manager.has_remaining_tasks()
            or payload_manager.remaining_task_count() < required_tasks
        ):
            payload_manager.reset()
        if payload_manager.pop_task() is None:
            raise BaseHTTPException(
                error_enum=ErrorCodeEnum.TOO_MANY_REQUESTS,
                message="No initialized key pairs, or out of tasks!",
            )

        runner_failed = challenge_utils.run_web_phase(
            payload_manager,
            session_count=expected_sessions,
            score_job_id=score_job_id,
        )
        final_score = payload_manager.finalize(
            timeout_score=config.challenge.session_timeout_score
        )
        logger.info(f"[run {payload_manager.run_id}] Final score: {final_score}")
        _set_latest_result(
            score=float(final_score),
            feedback=(
                "failed in web scoring phase"
                if runner_failed
                else "web scoring completed"
            ),
            phase="web",
            simple_bot_passed=True,
        )
        return float(final_score)


def _short_digest(*parts: str) -> str:
    hasher = hashlib.sha256()
    for part in parts:
        hasher.update((part or "").encode("utf-8"))
        hasher.update(b"\x00")
    return hasher.hexdigest()[:16]


@validate_call(config={"arbitrary_types_allowed": True})
def get_web(request: Request) -> HTMLResponse:
    nonce, public_key, active = payload_manager.claim_web_key()
    if not active:
        logger.warning(
            "/_web called with no active session key; serving a throwaway key"
        )
    templates = Jinja2Templates(directory=_SRC_DIR / "./templates/html")
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "session_id": nonce,
            "nonce": nonce,
            "public_key": public_key,
            "public_key_id": _short_digest(public_key),
            "config_hash": _short_digest(
                SCHEMA_VERSION,
                str(config.api.security.asymmetric.key_size),
            ),
            "schema_version": SCHEMA_VERSION,
        },
    )


@validate_call
def get_random_val(nonce: str) -> str:
    with payload_manager.claim_lock:
        current = payload_manager.cur_key_pair
        if not current:
            raise BaseHTTPException(
                error_enum=ErrorCodeEnum.BAD_REQUEST,
                message="No initialized key pair or out of keys.",
            )
        if current.nonce != nonce:
            raise BaseHTTPException(
                error_enum=ErrorCodeEnum.UNAUTHORIZED,
                message="Invalid nonce value!",
            )
        if not current.public_key:
            raise BaseHTTPException(
                error_enum=ErrorCodeEnum.TOO_MANY_REQUESTS,
                message="Nonce is already retrieved!",
            )
        public_key = payload_manager.get_nonce()
        payload_manager.pop_task()
        return public_key


def _score_payload(plain_data: dict) -> float:
    try:
        shape_ok, shape_reason = scoring.validate_shape(plain_data)
        if not shape_ok:
            logger.info(f"Layer 1 shape check rejected session: {shape_reason}")
            return config.challenge.gate_fail_score
        return scoring.score_with_metrics_processor(
            data=plain_data,
            error_score=config.challenge.metrics_processor_error_score,
        )
    except Exception as err:
        logger.error(f"Unexpected scoring error; recording error score: {err}")
        return config.challenge.metrics_processor_error_score


@validate_call
def eval_bot(data: str) -> None:
    outcome = payload_manager.process_eval(
        data,
        decrypt_fn=challenge_utils.decrypt,
        score_fn=_score_payload,
    )
    if outcome.status == "recorded":
        logger.info(f"Recorded session {outcome.session_id} score: {outcome.score}")
    elif outcome.status == "duplicate":
        logger.warning(f"Duplicate /_eval for session {outcome.session_id}")
    else:
        logger.warning("Unattributable /_eval payload; ignoring")


__all__ = ["get_task", "get_web", "get_random_val", "score", "get_result", "eval_bot"]
