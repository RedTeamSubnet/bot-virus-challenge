# -*- coding: utf-8 -*-

import hashlib
import pathlib
import threading
import uuid
from typing import Dict, Optional, Union

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
CategoryScores = Dict[str, float]
FeedbackCategories = Dict[str, str]
LatestResultValue = Union[float, str, bool, None, FeedbackCategories]

_latest_result: Dict[str, LatestResultValue] = {
    "score": None,
    "feedback": "",
    "phase": "not_started",
    "category_scores": {},
    "gate_passed": None,
    "gate_reason": None,
}
SCHEMA_VERSION = scoring.SCHEMA_VERSION
GATE_TIMEOUT_REASON = "timeout"


def _set_latest_result(
    *,
    score: float,
    feedback: str,
    phase: str,
    category_scores: FeedbackCategories | None = None,
    gate_passed: Optional[bool] = None,
    gate_reason: Optional[str] = None,
) -> None:
    with _latest_result_lock:
        _latest_result.update(
            {
                "score": score,
                "feedback": feedback,
                "phase": phase,
                "category_scores": category_scores or {},
                "gate_passed": gate_passed,
                "gate_reason": gate_reason,
            }
        )


FEEDBACK_OK_MIN = 0.85


def _feedback_band(score: float) -> str:
    # Two levels only: finer bands give miners a tuning signal.
    return "ok" if score >= FEEDBACK_OK_MIN else "needs_work"


def _miner_feedback(category_scores: CategoryScores) -> FeedbackCategories:
    # Caps (`limit`) are deliberately not reported; they stay in the logs.
    return {
        category: _feedback_band(score)
        for category, score in category_scores.items()
        if category in {"session", "client", "activity"}
    }


def get_result() -> Dict[str, LatestResultValue]:
    with _latest_result_lock:
        return dict(_latest_result)


def get_task() -> MinerInput:
    return MinerInput()


@validate_call
def score(miner_output: MinerOutput) -> float:
    """Build, verify, and score one miner submission."""
    expected_sessions = config.challenge.n_run_per_ch
    required_tasks = expected_sessions
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

        # Smoke session first: one session, inspected before spending the rest.
        smoke_runner_failed = challenge_utils.run_web_phase(
            payload_manager,
            session_count=1,
            score_job_id=score_job_id,
        )
        gate_passed: Optional[bool] = None
        runner_failed = smoke_runner_failed
        if not smoke_runner_failed:
            failed_session = payload_manager.first_hard_fail()
            gate_reason: Optional[str] = None
            if failed_session is not None:
                gate_reason = failed_session.hard_fail_reason
            elif payload_manager.completed_count() < 1:
                # The bot never submitted the smoke session in time.
                gate_reason = GATE_TIMEOUT_REASON
            if failed_session is not None or gate_reason is not None:
                logger.info(
                    f"[run {payload_manager.run_id}] Smoke session failed: {gate_reason}"
                )
                _set_latest_result(
                    score=0.0,
                    feedback="smoke session failed",
                    phase="gate",
                    gate_passed=False,
                    gate_reason=gate_reason,
                )
                return 0.0
            gate_passed = True
            if expected_sessions > 1:
                runner_failed = challenge_utils.run_web_phase(
                    payload_manager,
                    session_count=expected_sessions - 1,
                    score_job_id=score_job_id,
                    target_completed=expected_sessions,
                )
        if smoke_runner_failed:
            # Infrastructure failure before the gate decision. The score below
            # is still the plain average (policy unchanged pending owner
            # decision on runner_fail_score); flag it so it is not mistaken
            # for a completed evaluation.
            evaluation_status = "smoke_runner_failed"
            logger.warning(
                f"[run {payload_manager.run_id}] Runner failed during the smoke "
                "session; gate not evaluated, remaining sessions not run"
            )
        elif runner_failed:
            evaluation_status = "batch_runner_failed"
        else:
            evaluation_status = "completed"
        final_score = payload_manager.finalize(
            timeout_score=config.challenge.session_timeout_score
        )
        category_scores = payload_manager.finalize_category_scores()
        logger.info(
            f"[run {payload_manager.run_id}] Final score: {final_score}; "
            f"status={evaluation_status}; "
            f"completed={payload_manager.completed_count()}/{expected_sessions}; "
            f"scorer_fallbacks={payload_manager.fallback_count()}; "
            f"categories: {category_scores}"
        )
        _set_latest_result(
            score=float(final_score),
            feedback=(
                "failed in web scoring phase"
                if runner_failed
                else "web scoring completed"
            ),
            phase="web",
            category_scores=_miner_feedback(category_scores),
            gate_passed=gate_passed,
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
    task = payload_manager.issue_task(nonce)
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
            "task": task,
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


def _score_payload(plain_data: dict) -> scoring.ScoreResult:
    try:
        shape_ok, shape_reason = scoring.validate_shape(plain_data)
        if not shape_ok:
            logger.info(f"Layer 1 shape check rejected session: {shape_reason}")
            return scoring.ScoreResult(
                config.challenge.gate_fail_score, {}, True, "payload",
                score_source="gate",
            )
        return scoring.score_with_metrics_processor(
            data=plain_data,
            error_score=config.challenge.metrics_processor_error_score,
        )
    except Exception as err:
        logger.error(f"Unexpected scoring error; recording error score: {err}")
        return scoring.ScoreResult(
            config.challenge.metrics_processor_error_score, {},
            score_source="fallback", scorer_error_type="unexpected",
        )
    except BaseException as err:
        # A Rust panic surfaces as pyo3 PanicException (a BaseException), which
        # the handlers above do not catch. Behaviour is unchanged (re-raised,
        # session left unrecorded); this only makes the cause visible.
        if type(err).__name__ == "PanicException":
            logger.error(f"Scorer panic (scorer_error_type=panic): {err}")
        raise


@validate_call
def eval_bot(data: str, trusted_request: dict | None = None) -> None:
    outcome = payload_manager.process_eval(
        data,
        decrypt_fn=challenge_utils.decrypt,
        score_fn=_score_payload,
        trusted_context=trusted_request,
    )
    if outcome.status == "recorded":
        logger.info(
            f"Recorded session {outcome.session_id} score: {outcome.score}"
            f" source={outcome.score_source}"
            + (f" scorer_error={outcome.scorer_error_type}" if outcome.scorer_error_type else "")
            + (f" (hard fail: {outcome.hard_fail_reason})" if outcome.hard_fail else "")
        )
    elif outcome.status == "duplicate":
        logger.warning(f"Duplicate /_eval for session {outcome.session_id}")
    else:
        logger.warning("Unattributable /_eval payload; ignoring")


__all__ = ["get_task", "get_web", "get_random_val", "score", "get_result", "eval_bot"]
