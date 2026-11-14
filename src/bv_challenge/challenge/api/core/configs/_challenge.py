# -*- coding: utf-8 -*-

from typing import Literal, Optional

from pydantic import Field, SecretStr
from pydantic_settings import SettingsConfigDict

from api.core.constants import ENV_PREFIX
from ._base import FrozenBaseConfig


class ChallengeConfig(FrozenBaseConfig):
    bot_timeout: int = Field(default=120, ge=1)
    session_timeout_score: float = Field(default=0.0, ge=0.0, le=1.0)
    # Sessions scored per submission (one build, N runs of the bot).
    runs_per_submission: int = Field(default=1, ge=1, le=20)
    # A session scoring below this means the scorer detected automation: the
    # submission is rejected (score 0.0) and the remaining runs are skipped.
    # The detector's automation caps (0.0 / 0.35 / 0.45) all sit below 0.5.
    reject_below: float = Field(default=0.5, ge=0.0, le=1.0)
    # Sessions that time out or whose container fails may be re-run this many
    # times per SUBMISSION (not per session): one flaky run must not zero an
    # honest bot, and a bot must not get a fresh try for every bad session.
    session_retries: int = Field(default=1, ge=0, le=3)
    # How completed sessions combine: "min" (the worst session decides) or "mean".
    score_aggregation: Literal["min", "mean"] = Field(default="min")
    # What GET /result shows. "minimal": final score and a coarse phase only,
    # so a miner cannot tune a bot against per-session scores, category scores
    # or rejection reasons. "full": everything (local debugging only).
    feedback_detail: Literal["minimal", "full"] = Field(default="minimal")
    # Scorer base URL (e.g. http://scorer:8000). When set and a submission has
    # at least `runs_compare_min` passing sessions, the scorer compares them
    # (POST /api/v1/score/runs, X-API-Key = api_key) and a cap below
    # `reject_below` rejects the submission. Unset: sessions are not compared.
    scorer_endpoint: Optional[str] = Field(default=None)
    scorer_timeout: int = Field(default=30, ge=1)
    scorer_ssl_verify: bool = Field(default=True)
    runs_compare_min: int = Field(default=3, ge=2, le=20)
    # "shadow": the comparison runs and its verdict is logged, but it never
    # changes a score and a failed comparison is only a warning. "enforce":
    # the verdict rejects, and a failed comparison fails the request (503).
    # Roll out in shadow first.
    runs_compare_mode: Literal["shadow", "enforce"] = Field(default="shadow")
    vm_endpoint: str = Field(...)
    vm_timeout: int = Field(default=120, ge=1)
    vm_ssl_verify: bool = Field(default=True)
    # Sent as X-API-Key to the bot-runner VM (protects /build and /run-web).
    # min_length=9: bot-runner's auth_api_key rejects len <= 8.
    vm_api_key: SecretStr = Field(..., min_length=9, max_length=128)
    api_key: SecretStr = Field(..., min_length=8, max_length=128)

    model_config = SettingsConfigDict(env_prefix=f"{ENV_PREFIX}CHALLENGE_")


__all__ = ["ChallengeConfig"]
