# -*- coding: utf-8 -*-

import os
import time
import json
import pathlib
from typing import List, Union, Dict, Tuple

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
        self.reset_tasks()
        self.scores = []

    def reset_tasks(self) -> None:
        """Reset all tasks, regenerate key pairs and action lists"""
        self._actions_idx = 0

        # Generate key pairs
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

        # Reset current task properties
        self.cur_key_pair = None
        self.cur_score = None
        self.scores = []

    def pop_task(self) -> Union[KeyPairPM, List[Dict]]:
        """Get the next task (key pair and action list)"""
        if not self.key_pairs:
            return None

        self.cur_key_pair = self.key_pairs.pop(0)
        return self.cur_key_pair

    def has_remaining_tasks(self) -> bool:
        """Check if there are remaining tasks"""
        return len(self.key_pairs) > 0

    def get_remaining_task_count(self) -> int:
        """Get the number of remaining tasks"""
        return len(self.key_pairs)

    def record_score(self, score: float) -> None:
        """Record a score from the current session"""
        self.scores.append(score)

    def is_last_session(self) -> bool:
        """Check if this is the last session in the epoch"""
        _current_session = len(self.scores) + 1
        _total_sessions = config.challenge.n_run_per_ch
        logger.info(
            f"Session progress: {_current_session}/{_total_sessions} completed"
        )
        return _current_session == _total_sessions

    def get_nonce(self) -> str:
        _nonce_key: str = self.cur_key_pair.public_key
        self.cur_key_pair.public_key = None
        self.cur_key_pair.nonce = None
        return _nonce_key

    def get_private_key(self) -> str:
        _private_key: str = self.cur_key_pair.private_key
        self.cur_key_pair.private_key = None
        return _private_key

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
    """Score the miner output by sending to external VM"""
    _score = 0.0
    _num_tasks = config.challenge.n_ch_per_epoch * config.challenge.n_run_per_ch

    # Reset the task manager if needed
    if not tm.has_remaining_tasks():
        tm.reset_tasks()

    if tm.get_remaining_task_count() < _num_tasks:
        tm.reset_tasks()

    try:
        # Get the next task
        task = tm.pop_task()
        tm.cur_score = None
        if not task:
            raise BaseHTTPException(
                error_enum=ErrorCodeEnum.TOO_MANY_REQUESTS,
                message="No initialized key pairs or action lists, or out of tasks!",
            )

        # Send build and run request to external VM
        logger.info("Sending bot to external VM for build and execution...")
        _vm_response = ch_utils.send_build_and_run_request(
            vm_endpoint=config.challenge.vm_endpoint,
            bot_py=miner_output.bot_py,
            dockerfile=miner_output.dockerfile,
            session_count=config.challenge.n_run_per_ch,
            timeout=config.challenge.vm_timeout,
            ssl_verify=config.challenge.vm_ssl_verify,
            score_job_id=miner_output.score_job_id,
        )

        # Wait for all sessions to complete and metrics to be collected
        _i = 0
        while True:
            if len(tm.scores) >= config.challenge.n_run_per_ch:
                _score = sum(tm.scores) / len(tm.scores)
                logger.info("Successfully scored the miner output.")
                break

            logger.info(f"Waiting for the bot to finish... {tm.scores[-1] is not None}")
            time.sleep(1)
            _i += 1

            if config.challenge.bot_timeout < _i:
                logger.error("Timeout error: Bot running too long or failed to finish!")
                break

    except Exception as err:
        if isinstance(err, BaseHTTPException):
            raise

        logger.error(f"Failed to score the miner output: {str(err)}!")
        raise

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
    """Get the random value for the nonce"""
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
    return _nonce_key


@validate_call
def eval_bot(data: str) -> None:
    """Evaluate the bot performance"""
    if not tm.cur_key_pair:
        raise BaseHTTPException(
            error_enum=ErrorCodeEnum.BAD_REQUEST,
            message="Not initialized key pair or out of key pair, this endpoint is shouldn't be called directly!",
        )

    _private_key: str = tm.get_private_key()

    logger.debug("Evaluating the bot...")

    try:
        _plaintext = ch_utils.decrypt(ciphertext=data, private_key=_private_key)
        _plain_data = json.loads(_plaintext)


        _metrics_processor = "MetricsProcessor()"
        # _result = _metrics_processor(data=_plain_data)
        _cur_session_score = 0.5

        logger.info(f"Bot evaluation result: {_cur_session_score}")
        tm.cur_score = _cur_session_score
        tm.record_score(_cur_session_score)
        logger.info(f"Bot current score: {tm.cur_score}")

        # Reset for next epoch
        tm.cur_key_pair = None
        tm.pop_task()

        logger.debug("Successfully evaluated the bot.")
    except Exception as err:
        if isinstance(err, BaseHTTPException):
            raise

        logger.error(f"Failed to evaluate the bot: {str(err)}!")
        raise

    return


def compare_outputs(miner_input, miner_output, reference_output) -> dict:
    """
    Compare miner's output against a reference output using CFGAnalyser and CFGComparer.

    Args:
        miner_input (dict): The input used for both miner outputs.
        miner_output (dict): The output from the current miner (expects "bot_py" key).
        reference_output (dict): The reference output.

    Returns:
        dict: Similarity score and reason.
    """
    try:
        logger.info("Analyzing miner output...")

        _miner_code = miner_output["bot_py"]
        _reference_code = reference_output["bot_py"]

        if not _miner_code or not _reference_code:
            logger.error("Missing bot_py in miner_output or reference_output.")
            return {
                "similarity_score": 0.0,
                "reason": "Missing bot_py in miner_output or reference_output",
            }

        _result = """RTComparer().compare(
            challenge="bot-virus-challenge",
            miner_script=_miner_code,
            reference_script=_reference_code,
        )"""

        _similarity_score = _result.get("similarity_score", 0.0)
        _reason = _result.get("reason", "Unknown")
        logger.info(f"Similarity Score: {_similarity_score}")
        logger.info(f"Similarity Reason: {_reason}")

        try:
            _similarity_score = float(_similarity_score)
        except Exception:
            _similarity_score = 0.0

        return {"similarity_score": _similarity_score, "reason": _reason}

    except Exception as err:
        logger.error(f"Error in compare_outputs function: {str(err)}")
        return {"similarity_score": 0.0, "reason": str(err)}


__all__ = [
    "get_task",
    "get_web",
    "get_random_val",
    "score",
    "eval_bot",
    "compare_outputs",
]
