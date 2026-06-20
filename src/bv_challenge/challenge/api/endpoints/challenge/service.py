# -*- coding: utf-8 -*-

import os
import time
import pathlib
import threading
from typing import List, Union, Dict, Tuple, Optional

from pydantic import validate_call
from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
# from rt_comparer import RTComparer

# try:
#     from modules.rt_bv_score import MetricsProcessor  # type: ignore
# except ImportError:
#     from rt_bv_score import MetricsProcessor  # type: ignore

from api.core.constants import ErrorCodeEnum
from api.core import utils
from api.config import config
from api.core.exceptions import BaseHTTPException
from api.helpers.crypto import asymmetric as asymmetric_helper
from api.endpoints.challenge.schemas import KeyPairPM, MinerInput, MinerOutput
from api.endpoints.challenge import utils as ch_utils
from api.endpoints.challenge import scoring
from api.endpoints.challenge import eval_runner
from api.endpoints.challenge.session_store import RunStore
from api.logger import logger


_src_dir = pathlib.Path(__file__).parent.parent.parent.parent.resolve()


class TaskManager:
    """
    Task Manager for handling key pairs, action lists, and evaluation metrics
    during challenge sessions.
    """

    @validate_call
    def __init__(self, uid: str = None):
        self.uid = uid
        # Serializes whole scoring runs so concurrent /score calls cannot
        # clobber each other's run store.
        self.run_lock = threading.Lock()
        # Serializes the per-session key handoff (claim of the next session via
        # /_random_val). Distinct from run_lock, which /score holds for the whole
        # run -- the handoff happens *during* that hold, so it needs its own lock.
        self.claim_lock = threading.Lock()
        self.reset_tasks()

    def reset_tasks(self) -> None:
        """Reset all tasks: regenerate key pairs, action lists, and run store."""
        self._actions_idx = 0

        # Generate key pairs (one per session)
        self.key_pairs = ch_utils.gen_key_pairs(
            n_challenge=config.challenge.n_run_per_ch,
            key_size=config.api.security.asymmetric.key_size,
        )

        # Generate challenge actions
        self.challenges_action_list = ch_utils.gen_cb_actions(
            n_challenge=config.challenge.n_ch_per_epoch,
            window_width=config.challenge.window_width,
            window_height=config.challenge.window_height,
            n_checkboxes=config.challenge.n_checkboxes,
            min_distance=config.challenge.cb_min_distance,
            max_factor=config.challenge.cb_gen_max_factor,
            checkbox_size=config.challenge.cb_size,
            exclude_areas=config.challenge.cb_exclude_areas,
            pre_action_list=config.challenge.cb_pre_action_list,
        )

        # Build the run store. session_id = per-session nonce. Capture private
        # keys now, before they are consumed/nulled during the session flow, so
        # /_eval can attribute payloads by trial-decryption.
        self.run_id = utils.gen_random_string(length=16)
        _sessions: List[Tuple[str, str]] = [
            (kp.nonce, kp.private_key) for kp in self.key_pairs
        ]
        self.run_store = RunStore.create(run_id=self.run_id, sessions=_sessions)

        # Reset current task properties
        self.cur_key_pair = None
        self.cur_session_id: Optional[str] = None
        self.cur_score = None

    def pop_task(self) -> Union[KeyPairPM, None]:
        """Advance to the next session (key pair); capture its session id."""
        if not self.key_pairs:
            self.cur_key_pair = None
            self.cur_session_id = None
            return None

        self.cur_key_pair = self.key_pairs.pop(0)
        # Capture the session id now; the nonce field gets nulled later.
        self.cur_session_id = self.cur_key_pair.nonce
        return self.cur_key_pair

    def has_remaining_tasks(self) -> bool:
        """Check if there are remaining tasks"""
        return len(self.key_pairs) > 0

    def get_remaining_task_count(self) -> int:
        """Get the number of remaining tasks"""
        return len(self.key_pairs)

    def get_nonce(self) -> str:
        _nonce_key: str = self.cur_key_pair.public_key
        self.cur_key_pair.public_key = None
        self.cur_key_pair.nonce = None
        return _nonce_key

    def get_session_info(self) -> Dict:
        """Get information about current session for VM execution"""
        return {
            "total_sessions": config.challenge.n_run_per_ch,
            "nonce": self.cur_key_pair.nonce if self.cur_key_pair else None,
        }


# Initialize the task manager as a global variable
global tm
tm = TaskManager()


def get_task() -> MinerInput:
    """Get the task for the miner"""
    _miner_input = MinerInput()
    return _miner_input


@validate_call
def score(miner_output: MinerOutput) -> float:
    """Run one scoring run.

    Orchestration: create a run (run_id + N session ids) -> start the bot
    sessions via the runner -> wait for each session to report via /_eval ->
    average over the EXPECTED session count (missing sessions count as
    session_timeout_score). The run lock serializes concurrent /score calls.
    """
    _expected = config.challenge.n_run_per_ch
    _num_tasks = config.challenge.n_ch_per_epoch * _expected

    with tm.run_lock:
        # Start a fresh run if the previous one is exhausted.
        if (not tm.has_remaining_tasks()) or (
            tm.get_remaining_task_count() < _num_tasks
        ):
            tm.reset_tasks()

        _run_store = tm.run_store
        _run_id = tm.run_id

        # Claim the first session for the page-serving flow.
        task = tm.pop_task()
        tm.cur_score = None
        if not task:
            raise BaseHTTPException(
                error_enum=ErrorCodeEnum.TOO_MANY_REQUESTS,
                message="No initialized key pairs or action lists, or out of tasks!",
            )

        def _start_runner() -> None:
            logger.info(
                f"[run {_run_id}] Starting {_expected} bot session(s) via runner..."
            )
            ch_utils.send_build_and_run_request(
                vm_endpoint=config.challenge.vm_endpoint,
                bot_py=miner_output.bot_py,
                dockerfile=miner_output.dockerfile,
                session_count=_expected,
                timeout=config.challenge.vm_timeout,
                ssl_verify=config.challenge.vm_ssl_verify,
                score_job_id=miner_output.score_job_id,
            )

        def _wait_for_completion() -> None:
            # Wait for sessions to report via /_eval, bounded by bot_timeout.
            _i = 0
            while (
                _run_store.completed_count() < _expected
                and _i < config.challenge.bot_timeout
            ):
                logger.info(
                    f"[run {_run_id}] Waiting... "
                    f"{_run_store.completed_count()}/{_expected} sessions recorded"
                )
                time.sleep(1)
                _i += 1

        def _on_runner_error(err: Exception) -> None:
            logger.error(
                f"[run {_run_id}] Runner failed: {err}; returning runner_fail_score."
            )

        # Average over EXPECTED sessions; incomplete ones count as timeout.
        _score = eval_runner.run_scoring(
            store=_run_store,
            start_runner=_start_runner,
            wait_for_completion=_wait_for_completion,
            timeout_score=config.challenge.session_timeout_score,
            runner_fail_score=config.challenge.runner_fail_score,
            on_runner_error=_on_runner_error,
        )
        logger.info(
            f"[run {_run_id}] Final score (avg over {_expected} sessions): {_score}"
        )
        return _score


@validate_call(config={"arbitrary_types_allowed": True})
def get_web(request: Request) -> HTMLResponse:
    """Get the web interface for the challenge"""
    _nonce = None
    if tm.cur_key_pair:
        _nonce = tm.cur_key_pair.nonce
    else:
        _nonce = utils.gen_random_string()
        logger.warning(
            "Not initialized key pair, this endpoint is shouldn't be called directly!"
        )
    _key_pair: Tuple[str, str] = asymmetric_helper.gen_key_pair(
        key_size=config.api.security.asymmetric.key_size, as_str=True
    )
    _, _public_key = _key_pair
    _templates = Jinja2Templates(directory=(_src_dir / "./templates/html"))
    _html_response = _templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "nonce": _nonce,
            "public_key": _public_key,
            "actions_list": tm.challenges_action_list,
        },
    )
    return _html_response


@validate_call
def get_random_val(nonce: str) -> str:
    """Hand out the current session's public key and advance to the next session.

    This is the per-session *claim* point: validating the nonce, returning the
    key, and advancing the pointer are done atomically under ``claim_lock`` so
    that the serial key handoff is correct without ``/_eval`` ever touching the
    pointer. Attribution of the eventual callback is independent of this pointer
    (it is done by trial-decryption in ``eval_bot``).
    """
    with tm.claim_lock:
        if not tm.cur_key_pair:
            raise BaseHTTPException(
                error_enum=ErrorCodeEnum.BAD_REQUEST,
                message="Not initialized key pair or out of key pair, this endpoint is shouldn't be called directly!",
            )

        if tm.cur_key_pair.nonce != nonce:
            raise BaseHTTPException(
                error_enum=ErrorCodeEnum.UNAUTHORIZED,
                message="Invalid nonce value!",
            )

        if not tm.cur_key_pair.public_key:
            raise BaseHTTPException(
                error_enum=ErrorCodeEnum.TOO_MANY_REQUESTS,
                message="Nonce is already retrieved!",
            )

        _nonce_key = tm.get_nonce()
        # Advance the claim pointer to the next session for the next bot. The
        # session just handed out stays in run_store and is attributed by its
        # key at /_eval time.
        tm.pop_task()
        return _nonce_key


def _score_payload(plain_data: dict) -> float:
    """Layer 1 gate then Layer 2 scorer for one decrypted payload.

    Never raises: any unexpected error falls back to the configured error score
    so ``eval_bot`` can always record a result for an attributed session.
    """
    try:
        _passed, _reason = scoring.passes_gate(plain_data)
        if not _passed:
            logger.info(f"Layer 1 gate rejected session: {_reason}")
            return config.challenge.gate_fail_score
        return scoring.score_with_metrics_processor(
            data=plain_data,
            error_score=config.challenge.metrics_processor_error_score,
        )
    except Exception as err:
        logger.error(f"Unexpected scoring error; recording error_score: {err}")
        return config.challenge.metrics_processor_error_score


@validate_call
def eval_bot(data: str) -> None:
    """Evaluate exactly one browser session callback.

    Pure attribution + record -- it does NOT touch the session-claim pointer
    (``cur_key_pair``/``pop_task``); that lives in ``get_random_val``.

    Invariants:
      * Records each session's score EXACTLY ONCE; a duplicate/replayed callback
        is ignored and never double-counts (``RunStore.record`` is the atomic
        source of truth).
      * An unattributable payload (no current session key decrypts it --
        stale/previous-run/garbage/tampered) is logged and ignored; it never
        consumes or advances another session. A real session that never reports
        is handled by ``RunStore.finalize`` timeout padding instead.
      * Never raises; returns nothing (the endpoint returns a generic message
        only -- the real score is never exposed).
    """
    _store = tm.run_store
    if _store is None:
        logger.warning("eval_bot called with no active run; ignoring.")
        return

    _outcome = eval_runner.process_eval(
        _store,
        data,
        decrypt_fn=ch_utils.decrypt,
        score_fn=_score_payload,
    )

    if _outcome.status == "recorded":
        logger.info(f"Recorded session {_outcome.session_id} score: {_outcome.score}")
    elif _outcome.status == "duplicate":
        logger.warning(
            f"Duplicate /_eval for session {_outcome.session_id}; not re-recording."
        )
    else:  # "unattributable"
        logger.warning(
            "Unattributable /_eval payload (stale/garbage/tampered); ignoring."
        )

    return


def compare_outputs(miner_input, miner_output, reference_output) -> dict:
    """Disabled: the RTComparer backend is not wired into this build.

    The previous implementation never actually invoked a comparer (it built a
    string literal and then called ``.get`` on it), so every call silently
    returned ``similarity_score: 0.0``. Until a real comparer is integrated this
    raises so the caller surfaces an honest "not implemented" instead of a
    misleading zero score.
    """
    raise NotImplementedError("compare_outputs is not available in this build")


__all__ = [
    "get_task",
    "get_web",
    "get_random_val",
    "score",
    "eval_bot",
    "compare_outputs",
]
