# -*- coding: utf-8 -*-

import threading
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Union

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


# One submission at a time: the runner has a single miner image and container
# name, so two overlapping runs would build over and score each other.
_score_lock = threading.Lock()

_pending_lock = threading.Lock()
_pending_sessions: Dict[str, _PendingSession] = {}

_latest_result_lock = threading.Lock()
LatestResultValue = Union[
    float, int, str, None, Dict[str, float], List[Dict[str, Union[str, float]]]
]
_latest_result: Dict[str, LatestResultValue] = {
    "session_id": None,
    "score": None,
    "feedback": "",
    "phase": "not_started",
    "category_scores": {},
    "runs": 0,
    "sessions": [],
}


def _set_latest_result(
    *,
    session_id: str,
    score: float,
    feedback: str,
    phase: str,
    category_scores: Union[Dict[str, float], None] = None,
    runs: int = 1,
    sessions: Union[List[Dict[str, Union[str, float]]], None] = None,
) -> None:
    with _latest_result_lock:
        _latest_result.update(
            {
                "session_id": session_id,
                "score": score,
                "feedback": feedback,
                "phase": phase,
                "category_scores": dict(category_scores or {}),
                "runs": runs,
                "sessions": list(sessions or []),
            }
        )


def _full_result() -> Dict[str, LatestResultValue]:
    with _latest_result_lock:
        return dict(_latest_result)


# Phases a miner needs to fix a broken bot; none says what the detector saw.
_PUBLIC_FEEDBACK = {
    "not_started": "",
    "build": "failed to build miner container",
    "run": "miner container failed",
    "timeout": "a session did not submit in time",
    "rejected": "rejected",
    "completed": "scored",
    "infrastructure_error": "submission not scored; it will be retried",
}


def get_result() -> Dict[str, LatestResultValue]:
    """Latest result as shown to callers (see `feedback_detail`).

    "minimal" keeps the response keys but drops per-session scores, category
    scores, the deciding session id and every rejection detail: those are the
    signals a miner would tune a bot against. The full record stays in the
    service log.
    """
    full = _full_result()
    if config.challenge.feedback_detail == "full":
        return full
    phase = str(full.get("phase") or "not_started")
    return {
        "session_id": None,
        "score": full.get("score"),
        "feedback": _PUBLIC_FEEDBACK.get(phase, ""),
        "phase": phase,
        "category_scores": {},
        "runs": full.get("runs", 0),
        "sessions": [],
    }


def _register_session(session_id: str) -> _PendingSession:
    pending = _PendingSession(event=threading.Event())
    with _pending_lock:
        _pending_sessions[session_id] = pending
    return pending


def _unregister_session(session_id: str) -> None:
    with _pending_lock:
        _pending_sessions.pop(session_id, None)


@dataclass
class _SessionOutcome:
    session_id: str
    score: float
    phase: str
    feedback: str
    category_scores: Dict[str, float] = field(default_factory=dict)

    def summary(self) -> Dict[str, Union[str, float]]:
        return {"session_id": self.session_id, "score": self.score, "phase": self.phase}


def _run_session(session_id: str, keep_image: bool) -> _SessionOutcome:
    """Run the already-built bot once and wait for the scorer's callback."""
    pending = _register_session(session_id)
    try:
        try:
            challenge_utils.send_run_web_request(session_id, keep_image=keep_image)
        except Exception as err:
            logger.error(f"[{session_id}] runner failed: {err}")
            return _SessionOutcome(
                session_id, 0.0, "run", "failed to run miner container"
            )

        signaled = pending.event.wait(timeout=config.challenge.bot_timeout)
        if signaled and pending.score is not None:
            logger.info(f"[{session_id}] scorer returned {pending.score}")
            return _SessionOutcome(
                session_id,
                float(pending.score),
                "completed",
                pending.feedback or "scored",
                dict(pending.category_scores),
            )
        logger.warning(
            f"[{session_id}] no scorer callback within {config.challenge.bot_timeout}s"
        )
        return _SessionOutcome(
            session_id,
            float(config.challenge.session_timeout_score),
            "timeout",
            "scorer timed out",
        )
    finally:
        _unregister_session(session_id)


def _aggregate(outcomes: List[_SessionOutcome]) -> float:
    scores = [o.score for o in outcomes]
    if config.challenge.score_aggregation == "mean":
        return sum(scores) / len(scores)
    return min(scores)


def _compare_sessions(
    build_id: str, outcomes: List[_SessionOutcome]
) -> Union[float, None]:
    """Cross-run cap from the scorer, or None when it finds no automation.

    In "shadow" mode the verdict is only logged. In "enforce" mode a
    comparison that cannot be made is an infrastructure failure: the request
    fails with 503 and no score, so a scorer outage can neither pass a bot nor
    zero an honest submission.
    """
    cfg = config.challenge
    if not cfg.scorer_endpoint or len(outcomes) < cfg.runs_compare_min:
        return None
    enforce = cfg.runs_compare_mode == "enforce"
    try:
        verdict = challenge_utils.compare_runs([o.session_id for o in outcomes])
    except Exception as err:
        if not enforce:
            logger.warning(
                f"[{build_id}] run comparison failed (shadow, ignored): {err}"
            )
            return None
        logger.error(f"[{build_id}] run comparison failed: {err}")
        _set_latest_result(
            session_id=build_id,
            score=0.0,
            feedback="run comparison unavailable; submission not scored",
            phase="infrastructure_error",
            runs=len(outcomes),
        )
        raise BaseHTTPException(
            error_enum=ErrorCodeEnum.SERVICE_UNAVAILABLE,
            message="Run comparison is unavailable; the submission was not scored.",
        )
    cap = verdict.get("score_cap")
    logger.info(
        f"[{build_id}] run comparison ({cfg.runs_compare_mode}): level={verdict['level']} cap={cap} "
        f"worst_session={min(o.score for o in outcomes):.3f}"
    )
    if not enforce:
        return None
    return float(cap) if cap is not None else None


@validate_call
def score(miner_output: MinerOutput) -> ScoreResponse:
    """Build the bot once, run it `runs_per_submission` times, score the runs.

    Every run is a fresh session scored on its own. The first session below
    `reject_below` (automation detected, timeout or runner failure) rejects
    the submission with 0.0 and skips the remaining runs. Otherwise the
    sessions combine with `score_aggregation` ("min": the worst one decides),
    after the scorer has compared them with each other (`scorer_endpoint`): a
    bot can pass every session and still give itself away across sessions.
    """
    if not _score_lock.acquire(blocking=False):
        raise BaseHTTPException(
            error_enum=ErrorCodeEnum.TOO_MANY_REQUESTS,
            message="Another submission is being scored; retry later.",
        )
    try:
        return _score_submission(miner_output)
    finally:
        _score_lock.release()


def _score_submission(miner_output: MinerOutput) -> ScoreResponse:
    runs = config.challenge.runs_per_submission
    reject_below = config.challenge.reject_below
    build_id = uuid.uuid4().hex
    logger.info(f"[{build_id}] starting score run: {runs} session(s)")
    bot_py = miner_output.get_file("bot.py")
    dockerfile = miner_output.get_file("Dockerfile")

    try:
        challenge_utils.send_build_request(bot_py, dockerfile, build_id)
    except Exception as err:
        logger.error(f"[{build_id}] build failed: {err}")
        _set_latest_result(
            session_id=build_id,
            score=0.0,
            feedback="failed to build miner container",
            phase="build",
            runs=runs,
        )
        return ScoreResponse(session_id=build_id, score=0.0)

    outcomes: List[_SessionOutcome] = []
    rejected: Union[_SessionOutcome, None] = None
    retries_left = config.challenge.session_retries
    for k in range(runs):
        keep_image = k < runs - 1
        outcome = _run_session(
            uuid.uuid4().hex, keep_image=keep_image or retries_left > 0
        )
        while outcome.phase != "completed" and retries_left > 0:
            # Timeout or container failure: possibly infrastructure, so the
            # session is run again. A scored session is never re-run.
            retries_left -= 1
            logger.warning(
                f"[{build_id}] session {k + 1}/{runs} {outcome.phase}; re-running "
                f"({retries_left} retry(ies) left)"
            )
            outcome = _run_session(
                uuid.uuid4().hex, keep_image=keep_image or retries_left > 0
            )
        outcomes.append(outcome)
        if outcome.score < reject_below:
            rejected = outcome
            break

    if rejected is not None:
        n = len(outcomes)
        if n < runs:
            # The runner kept the image for a run that will not happen; its
            # next /build starts with a full cleanup.
            logger.info(f"[{build_id}] skipping {runs - n} remaining run(s)")
        final_score = 0.0
        decisive = rejected
        phase = "rejected" if rejected.phase == "completed" else rejected.phase
        feedback = (
            f"rejected: session {n}/{runs} {rejected.phase} with score "
            f"{rejected.score:.3f} (< {reject_below}); {rejected.feedback}"
        )
    else:
        decisive = min(outcomes, key=lambda o: o.score)
        cap = _compare_sessions(build_id, outcomes)
        if cap is not None and cap < reject_below:
            final_score = 0.0
            phase = "rejected"
            # Deliberately coarse: which pattern gave the bot away stays private.
            feedback = f"rejected: the {runs} sessions together show automation"
        else:
            final_score = _aggregate(outcomes)
            if cap is not None:
                final_score = min(final_score, cap)
            phase = "completed"
            feedback = (
                f"scored {runs} session(s), {config.challenge.score_aggregation}="
                f"{final_score:.3f}; worst session: {decisive.feedback}"
            )
    logger.info(f"[{build_id}] final score {final_score} ({phase})")
    _set_latest_result(
        session_id=decisive.session_id,
        score=final_score,
        feedback=feedback[:512],
        phase=phase,
        category_scores=decisive.category_scores,
        runs=runs,
        sessions=[o.summary() for o in outcomes],
    )
    return ScoreResponse(session_id=decisive.session_id, score=final_score)


@validate_call
def record_payload(payload: PayloadRequest) -> None:
    with _pending_lock:
        pending = _pending_sessions.get(payload.session_id)
    if pending is None:
        raise BaseHTTPException(
            error_enum=ErrorCodeEnum.NOT_FOUND,
            message="Unknown or expired session_id",
        )
    with _pending_lock:
        # First result wins: a later callback must not replace a reported score.
        if pending.event.is_set():
            raise BaseHTTPException(
                error_enum=ErrorCodeEnum.CONFLICT,
                message="A score was already recorded for this session_id",
            )
        pending.score = payload.score
        pending.category_scores = dict(payload.category_scores)
        pending.feedback = payload.feedback
        pending.event.set()
    logger.info(f"[{payload.session_id}] scorer POSTed score={payload.score}")


__all__ = ["score", "record_payload", "get_result"]
