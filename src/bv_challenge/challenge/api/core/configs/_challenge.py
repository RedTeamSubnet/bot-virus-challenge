# -*- coding: utf-8 -*-

from pydantic import Field, SecretStr
from pydantic_settings import SettingsConfigDict

from api.core.constants import ENV_PREFIX
from ._base import FrozenBaseConfig


class ChallengeConfig(FrozenBaseConfig):
    n_run_per_ch: int = Field(...)
    bot_timeout: int = Field(..., ge=1)
    web_check_enabled: bool = Field(default=True)
    # Layer 1/2 fallback score policy (not detector secrets — just policy).
    gate_fail_score: float = Field(default=0.0, ge=0.0, le=1.0)
    metrics_processor_error_score: float = Field(default=0.5, ge=0.0, le=1.0)
    session_timeout_score: float = Field(default=0.0, ge=0.0, le=1.0)
    runner_fail_score: float = Field(default=0.0, ge=0.0, le=1.0)
    # Bot container lifecycle, run by this service against the DinD sidecar.
    commit_dir: str = Field(default="/commit")
    miner_image_tag: str = Field(default="redteamsubnet/bv-miner:latest")
    challenge_network_name: str = Field(default="bot-challenge-network")
    # How long to wait for a stale container left allocated by a previous run
    # before force-removing it.
    container_run_timeout_sec: int = Field(default=10, ge=0)
    # How long to wait for the container this run just started to exit.
    # Without it a hung bot holds the shared container name forever. 0
    # disables the timeout.
    container_wait_timeout_sec: int = Field(default=900, ge=0)
    api_key: SecretStr = Field(..., min_length=8, max_length=128)

    model_config = SettingsConfigDict(env_prefix=f"{ENV_PREFIX}CHALLENGE_")


__all__ = ["ChallengeConfig"]
