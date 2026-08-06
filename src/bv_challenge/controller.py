import os

import bittensor as bt

from redteam_core.challenge_pool.controller import Controller
from redteam_core.validator.models import MinerChallengeCommit
import requests


class BVController(Controller):

    def __init__(
        self,
        challenge_name: str,
        challenge_info: dict,
        miner_commits: list[MinerChallengeCommit],
        reference_comparison_commits: list[MinerChallengeCommit],
        miners_docker_info: dict[str, dict],
        seed_inputs: list[dict] = [],
    ):

        super().__init__(
            challenge_name,
            challenge_info,
            miner_commits,
            reference_comparison_commits,
            miners_docker_info,
            seed_inputs,
        )
        comparison_config = self.challenge_info.get("comparison_config", {})
        self.comparison_min_acceptable_score = comparison_config.get(
            "min_acceptable_score", 0.6
        )

    def _score_miner_with_new_inputs(
        self, miner_commit: MinerChallengeCommit, challenge_inputs
    ) -> None:
        _scoring_log = miner_commit.scoring_logs[0]
        _higest_comparison_score = miner_commit.get_higest_comparison_score()
        if (
            _higest_comparison_score >= self.comparison_min_acceptable_score
            or _higest_comparison_score == 0.0
        ):
            bt.logging.info(
                f"[CONTROLLER - ABSController] Skipping scoring for miner {miner_commit.miner_hotkey} on task "
                f"due to high comparison score: {_higest_comparison_score}"
            )
            _scoring_log.score = 0.0
            if _scoring_log.error:
                _scoring_log.error += " | Skipped scoring due to high comparison score."
            else:
                _scoring_log.error = "Skipped scoring due to high comparison score."
            return

        score = (
            self._score_challenge(
                miner_input=challenge_inputs[0],
                miner_output=_scoring_log.miner_output,
                task_id=0,
            )
            if _scoring_log.miner_output is not None
            else 0.0
        )

        _scoring_log.score = score
        _result_response = self._get_results_from_challenge()
        _scoring_log.miner_output["scoring_results"] = _result_response
        # _scoring_log.miner_output["telemetry"] = self._get_telemetry_from_challenge()
        return

    def _get_results_from_challenge(self) -> dict:
        result_url = "http://localhost:10001/result"
        api_key = os.environ.get("RT_CHALLENGE_API_KEY")
        try:
            response = requests.get(
                result_url, timeout=5, verify=False, headers={"X-API-Key": api_key}
            )  # nosec
            response.raise_for_status()
            _result_response = response.json() if response.content else {}
            return _result_response
        except Exception as exc:
            bt.logging.error(
                f"[CONTROLLER] Unable to fetch result from challenge endpoint: {exc}"
            )
            return {}

    # def _get_telemetry_from_challenge(self) -> dict:
    #     telemetry_url = "http://localhost:10001/telemetry"
    #     try:
    #         response = requests.get(telemetry_url, timeout=5, verify=False)  # nosec
    #         response.raise_for_status()
    #         return response.json() if response.content else {}
    #     except Exception as exc:
    #         bt.logging.error(
    #             f"[CONTROLLER] Unable to fetch telemetry from challenge endpoint: {exc}"
    #         )
    #         return {}

    def _exclude_output_keys(self, miner_output: dict, reference_output: dict) -> None:
        miner_output["commit_files"] = None
        reference_output["commit_files"] = None
        # miner_output["telemetry"] = None
        # reference_output["telemetry"] = None
        miner_output["scoring_results"] = None
        reference_output["scoring_results"] = None
        return


__all__ = [
    "MyController",
]
