# -*- coding: utf-8 -*-

import threading
import uuid
from dataclasses import dataclass, field
from typing import Dict, Union

from pydantic import validate_call

from api.config import config
from api.core.constants import ErrorCodeEnum
from api.core.exceptions import BaseHTTPException
from api.endpoints.challenge import utils as challenge_utils
from api.endpoints.challenge.schemas import (
    MinerOutput,
    PayloadRequest,
    ScoreResponse,
)
from api.logger import logger


@dataclass
class _PendingSession:
    event: threading.Event
    score: Union[float, None] = None
    category_scores: Dict[str, float] = field(default_factory=dict)
    feedback: Union[str, None] = None


_pending_lock = threading.Lock()
_pending_sessions: Dict[str, _PendingSession] = {}

_latest_result_lock = threading.Lock()
LatestResultValue = Union[float, str, None, Dict[str, float]]
_latest_result: Dict[str, LatestResultValue] = {
    "session_id": None,
    "score": None,
    "feedback": "",
    "phase": "not_started",
    "category_scores": {},
}


def _set_latest_result(
    *,
    session_id: str,
    score: float,
    feedback: str,
    phase: str,
    category_scores: Union[Dict[str, float], None] = None,
) -> None:
    with _latest_result_lock:
        _latest_result.update(
            {
                "session_id": session_id,
                "score": score,
                "feedback": feedback,
                "phase": phase,
                "category_scores": dict(category_scores or {}),
            }
        )


def get_result() -> Dict[str, LatestResultValue]:
    with _latest_result_lock:
        return dict(_latest_result)


def _register_session(session_id: str) -> _PendingSession:
    pending = _PendingSession(event=threading.Event())
    with _pending_lock:
        _pending_sessions[session_id] = pending
    return pending


def _unregister_session(session_id: str) -> None:
    with _pending_lock:
        _pending_sessions.pop(session_id, None)


@validate_call
def score(miner_output: MinerOutput) -> ScoreResponse:
    session_id = uuid.uuid4().hex
    logger.info(f"[{session_id}] starting score run")
    pending = _register_session(session_id)
    try:
        bot_py = miner_output.get_file("bot.py")
        dockerfile = miner_output.get_file("Dockerfile")

        try:
            challenge_utils.send_build_request(bot_py, dockerfile, session_id)
        except Exception as err:
            logger.error(f"[{session_id}] build failed: {err}")
            _set_latest_result(
                session_id=session_id,
                score=0.0,
                feedback="failed to build miner container",
                phase="build",
            )
            return ScoreResponse(session_id=session_id, score=0.0)

        try:
            challenge_utils.send_run_web_request(session_id)
        except Exception as err:
            logger.error(f"[{session_id}] runner failed: {err}")
            _set_latest_result(
                session_id=session_id,
                score=0.0,
                feedback="failed to run miner container",
                phase="run",
            )
            return ScoreResponse(session_id=session_id, score=0.0)

        signaled = pending.event.wait(timeout=config.challenge.bot_timeout)
        if signaled and pending.score is not None:
            final_score = float(pending.score)
            feedback = pending.feedback or "scored"
            phase = "completed"
            category_scores = pending.category_scores
            logger.info(f"[{session_id}] scorer returned {final_score}")
        else:
            final_score = float(config.challenge.session_timeout_score)
            feedback = "scorer timed out"
            phase = "timeout"
            category_scores = {}
            logger.warning(
                f"[{session_id}] no scorer callback within {config.challenge.bot_timeout}s"
            )

        _set_latest_result(
            session_id=session_id,
            score=final_score,
            feedback=feedback,
            phase=phase,
            category_scores=category_scores,
        )
        return ScoreResponse(session_id=session_id, score=final_score)
    finally:
        _unregister_session(session_id)


@validate_call
def record_payload(payload: PayloadRequest) -> None:
    with _pending_lock:
        pending = _pending_sessions.get(payload.session_id)
    if pending is None:
        raise BaseHTTPException(
            error_enum=ErrorCodeEnum.NOT_FOUND,
            message="Unknown or expired session_id",
        )
    pending.score = payload.score
    pending.category_scores = dict(payload.category_scores)
    pending.feedback = payload.feedback
    pending.event.set()
    logger.info(
        f"[{payload.session_id}] scorer POSTed score={payload.score}"
    )


__all__ = ["score", "record_payload", "get_result"]
