# -*- coding: utf-8 -*-

import hashlib
import pathlib
import threading
import time
import uuid
from typing import Any, Dict, Optional, Union

import requests
from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import validate_call

from api.config import config
from api.core.constants import ErrorCodeEnum
from api.core.exceptions import BaseHTTPException
from api.endpoints.challenge import evidence as evidence_mod
from api.endpoints.challenge import provenance, scoring, scorer_client
from api.endpoints.challenge import utils as challenge_utils
from api.endpoints.challenge.payload_manager import (
    EvidencePolicy,
    PayloadManager,
    SessionContext,
)
from api.endpoints.challenge.versioning import DEFAULT_POLICY
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
    score: Optional[float],
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


# Signed provenance of the latest run, for validators only (API-key protected
# endpoint). It holds per-category scores, so it must never reach miners.
_provenance_lock = threading.Lock()
_latest_provenance: Dict[str, Any] = {}


def get_provenance() -> Dict[str, Any]:
    with _provenance_lock:
        return dict(_latest_provenance)


def _publish_provenance(final_score: float) -> None:
    signer = _request_signer()
    results = payload_manager.provenance_records()
    sessions = []
    for record in payload_manager.sessions.values():
        if not record.completed:
            status = "timeout"
        elif record.provenance:
            status = record.provenance["body"]["decision"]
        else:
            status = record.outcome or "unscored"
        sessions.append(
            {
                "session_id": record.session_id,
                "status": status,
                "score": record.score if record.completed else None,
                "evidence_status": record.evidence_status,
                "result_digest": provenance.digest(record.provenance) if record.provenance else None,
            }
        )
    authoritative = signer is not None and all(
        s["status"] == "timeout" or s["result_digest"] for s in sessions
    )
    manifest = None
    if signer is not None:
        now = int(time.time())
        manifest = signer.sign(
            provenance.DOMAIN_RUN_MANIFEST,
            {
                "type": "bv.run-manifest",
                "issued_at": now,
                "expires_at": now + 24 * 3600,
                "run_id": payload_manager.run_id,
                "submission_id": payload_manager.submission_id,
                "miner_id": payload_manager.miner_id,
                "aggregation": "mean",
                "timeout_score": config.challenge.session_timeout_score,
                "gate_fail_score": config.challenge.gate_fail_score,
                "final_score": float(final_score),
                "authoritative": authoritative,
                "sessions": sessions,
            },
        )
    with _provenance_lock:
        _latest_provenance.clear()
        _latest_provenance.update(
            {"authoritative": authoritative, "manifest": manifest, "results": results}
        )


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
        _configure_evidence()
        try:
            challenge_utils.send_build_request(bot_py, dockerfile, score_job_id)
        except requests.RequestException as err:
            # The runner could not be reached: not the miner's fault.
            _abort_run("infrastructure_error", f"build_transport:{type(err).__name__}")
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
        payload_manager.bind_submission(
            submission_id=submission_digest(miner_output),
            miner_id=miner_output.miner_id or "unbound",
        )

        # Smoke session first: one session, inspected before spending the rest.
        smoke_runner_failed = challenge_utils.run_web_phase(
            payload_manager,
            session_count=1,
            score_job_id=score_job_id,
        )
        gate_passed: Optional[bool] = None
        runner_failed = smoke_runner_failed
        _abort_if_untrustworthy(runner_failed, stage="smoke")
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
        # DH: a partial run (runner failure) is aborted for a full re-run,
        # and a scorer infrastructure or security failure aborts the run;
        # neither ever becomes a score.
        _abort_if_untrustworthy(runner_failed, stage="batch")
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
        _publish_provenance(final_score)
        _set_latest_result(
            score=float(final_score),
            feedback="web scoring completed",
            phase="web",
            category_scores=_miner_feedback(category_scores),
            gate_passed=gate_passed,
        )
        return float(final_score)


class RunAborted(BaseHTTPException):
    """The run produced no score. Validators must re-run it, never record one."""


def _abort_run(outcome: str, reason: str) -> None:
    run_id = payload_manager.run_id
    logger.error(f"[run {run_id}] Run aborted: outcome={outcome} reason={reason}")
    with _provenance_lock:
        _latest_provenance.clear()
    _set_latest_result(score=None, feedback="run aborted", phase="aborted")
    detail = {"outcome": outcome, "reason": reason, "run_id": run_id}
    aborted = RunAborted(
        error_enum=(
            ErrorCodeEnum.SERVICE_UNAVAILABLE
            if outcome == "infrastructure_error"
            else ErrorCodeEnum.INTERNAL_SERVER_ERROR
        ),
        message=f"Run aborted: {outcome}",
        detail=detail,
    )
    aborted.outcome = detail
    raise aborted


def _abort_if_untrustworthy(runner_failed: bool, *, stage: str) -> None:
    aborting = payload_manager.aborting_session()
    if aborting is not None:
        _abort_run(aborting.outcome, aborting.scorer_error_type or "scorer")
    if runner_failed:
        _abort_run("infrastructure_error", f"runner_failed:{stage}")


def submission_digest(miner_output: MinerOutput) -> str:
    """Content address of the submission: the exact files that were built."""
    return provenance.digest(
        sorted(
            [item.file_name, item.content] for item in miner_output.commit_files
        )
    )


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
    _configure_evidence()
    task = payload_manager.issue_task(nonce, observe(request))
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


_scorer_lock = threading.Lock()
_scorer_resolved = False
_scorer = None
_client: Optional[scorer_client.AuthoritativeScorerClient] = None
_signer: Optional[provenance.Signer] = None
_runner_secret: Optional[bytes] = None
_evidence_configured = False


def _read_secret_file(path: str) -> bytes:
    secret = pathlib.Path(path).read_bytes().strip()
    if len(secret) < 32:
        raise ValueError("runner attestation secret must be at least 32 bytes")
    return secret


def _configure_evidence() -> None:
    """Turn on the B6 evidence checks when attestation is configured."""
    global _evidence_configured, _runner_secret
    with _scorer_lock:
        if _evidence_configured:
            return
        ch = config.challenge
        if ch.runner_attestation_secret_file:
            _runner_secret = _read_secret_file(ch.runner_attestation_secret_file)
        if _runner_secret is not None or ch.attestation_required:
            payload_manager.evidence_policy = EvidencePolicy(
                require_attestation=ch.attestation_required,
                session_ttl_ms=ch.bot_timeout * 1000,
                max_payload_bytes=ch.max_payload_bytes,
                version_policy=DEFAULT_POLICY,
            )
        _evidence_configured = True


def observe(request: Request) -> evidence_mod.RequestObservation:
    return evidence_mod.observe_request(
        request.headers,
        now_ms=int(time.time() * 1000),
        runner_secret=_runner_secret,
        client_host=request.client.host if request.client else None,
    )


def _request_signer() -> Optional[provenance.Signer]:
    global _signer
    ch = config.challenge
    if _signer is None and ch.scorer_signing_key_file and ch.scorer_signing_kid:
        _signer = provenance.Signer.from_file(
            ch.scorer_signing_key_file, ch.scorer_signing_kid, "bv-challenge-api"
        )
    return _signer


def _authoritative_client() -> Optional[scorer_client.AuthoritativeScorerClient]:
    """The signed mTLS scorer client when remote scoring is configured."""
    global _client
    ch = config.challenge
    remote = ch.scorer_backend == "remote" or (ch.scorer_backend == "auto" and ch.scorer_url)
    if not remote:
        return None
    with _scorer_lock:
        if _client is None:
            http = requests.Session()
            if ch.scorer_url.startswith("https://"):
                http.verify = ch.scorer_ca_file
                http.cert = (ch.scorer_client_cert_file, ch.scorer_client_key_file)
            _client = scorer_client.AuthoritativeScorerClient(
                ch.scorer_url,
                _request_signer(),
                provenance.Keyring.from_file(ch.scorer_keyring_file),
                DEFAULT_POLICY,
                http=http,
                allowed_build_hashes=ch.scorer_allowed_build_hashes or None,
                timeout=ch.scorer_timeout,
                max_retries=ch.scorer_max_retries,
            )
            logger.info("Scorer backend: remote (signed, authoritative)")
        return _client


def _metrics_processor():
    """The development scorer, resolved once (see ``scoring.resolve_metrics_processor``)."""
    global _scorer, _scorer_resolved
    with _scorer_lock:
        if not _scorer_resolved:
            ch = config.challenge
            _scorer = scoring.resolve_metrics_processor(ch.scorer_backend, ch.scorer_url)
            _scorer_resolved = True
            name = type(_scorer).__name__ if _scorer else "local rt_bv_score wheel"
            logger.info(f"Scorer backend: {ch.scorer_backend} -> {name}")
        return _scorer


def _score_authoritative(
    client: scorer_client.AuthoritativeScorerClient, plain_data: dict, context: SessionContext
) -> scoring.ScoreResult:
    envelope = client.build_request(
        run_id=context.run_id,
        session_id=context.session_id,
        submission_id=context.submission_id,
        miner_id=context.miner_id,
        bundle=context.bundle,
        versions=context.versions,
    )
    try:
        result, signed = client.score(envelope, context.bundle)
    except scorer_client.ScorerInfrastructureError as err:
        logger.error(f"Scorer infrastructure error: {err}")
        return scoring.ScoreResult(
            0.0, {}, score_source="fallback", scorer_error_type=err.kind,
            outcome="infrastructure_error", evidence_status=context.evidence_status,
        )
    except scorer_client.ScorerSecurityError as err:
        logger.error(f"Scorer result rejected (security): {err}")
        return scoring.ScoreResult(
            0.0, {}, score_source="fallback", scorer_error_type=err.code,
            outcome="security_violation", evidence_status=context.evidence_status,
        )
    if result["decision"] == "invalid":
        reason = result.get("hard_fail_reason")
        return scoring.ScoreResult(
            config.challenge.gate_fail_score, {}, True,
            reason if reason in scoring.HARD_FAIL_REASONS else "integrity",
            score_source="scorer", outcome="invalid",
            evidence_status=context.evidence_status,
            evidence_reasons=tuple(result.get("reasons") or ()), provenance=signed,
        )
    scored = scoring.score_with_metrics_processor(
        data=plain_data,
        metrics_processor=lambda _data: result,
        error_score=config.challenge.metrics_processor_error_score,
    )
    if scored.outcome != "scored":
        # Signed but unusable (e.g. a non-numeric score): a scorer defect.
        return scored._replace(outcome="security_violation", evidence_status=context.evidence_status)
    return scored._replace(evidence_status=context.evidence_status, provenance=signed)


def _score_payload(plain_data: dict, context: Optional[SessionContext] = None) -> scoring.ScoreResult:
    try:
        shape_ok, shape_reason = scoring.validate_shape(plain_data)
        if not shape_ok:
            logger.info(f"Layer 1 shape check rejected session: {shape_reason}")
            return scoring.ScoreResult(
                config.challenge.gate_fail_score, {}, True, "payload",
                score_source="gate", outcome="invalid",
                evidence_status=context.evidence_status if context else None,
            )
        client = _authoritative_client()
        if client is not None:
            if context is None or context.evidence_status != evidence_mod.STATUS_VALID:
                # Unreachable with a valid config (remote requires attestation);
                # kept so a misconfiguration can never score unverified evidence.
                return scoring.ScoreResult(
                    config.challenge.gate_fail_score, {}, True, "integrity",
                    score_source="gate", outcome="invalid",
                )
            return _score_authoritative(client, plain_data, context)
        result = scoring.score_with_metrics_processor(
            data=plain_data,
            metrics_processor=_metrics_processor(),
            error_score=config.challenge.metrics_processor_error_score,
        )
        if context is not None:
            result = result._replace(evidence_status=context.evidence_status)
            if result.outcome == "scored" and context.evidence_status != evidence_mod.STATUS_VALID:
                result = result._replace(outcome="review")
        return result
    except Exception as err:
        logger.error(f"Unexpected scoring error; run will abort: {err}")
        return scoring.ScoreResult(
            config.challenge.metrics_processor_error_score, {},
            score_source="fallback", scorer_error_type="unexpected",
            outcome="infrastructure_error",
        )
    except BaseException as err:
        # A Rust panic surfaces as pyo3 PanicException (a BaseException), which
        # the handlers above do not catch. Behaviour is unchanged (re-raised,
        # session left unrecorded); this only makes the cause visible.
        if type(err).__name__ == "PanicException":
            logger.error(f"Scorer panic (scorer_error_type=panic): {err}")
        raise


@validate_call(config={"arbitrary_types_allowed": True})
def eval_bot(
    data: str,
    trusted_request: dict | None = None,
    observation: Optional[evidence_mod.RequestObservation] = None,
) -> None:
    _configure_evidence()
    outcome = payload_manager.process_eval(
        data,
        decrypt_fn=challenge_utils.decrypt,
        score_fn=_score_payload,
        trusted_context=trusted_request,
        eval_observation=observation,
    )
    if outcome.status == "recorded":
        logger.info(
            f"Recorded session {outcome.session_id} score: {outcome.score}"
            f" outcome={outcome.outcome} evidence={outcome.evidence_status}"
            f" source={outcome.score_source}"
            + (f" scorer_error={outcome.scorer_error_type}" if outcome.scorer_error_type else "")
            + (f" (hard fail: {outcome.hard_fail_reason})" if outcome.hard_fail else "")
        )
    elif outcome.status == "duplicate":
        logger.warning(f"Duplicate /_eval for session {outcome.session_id}")
    else:
        logger.warning("Unattributable /_eval payload; ignoring")


__all__ = ["get_task", "get_web", "get_random_val", "score", "get_result", "get_provenance", "eval_bot", "observe"]
