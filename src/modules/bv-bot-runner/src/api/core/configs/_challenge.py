# -*- coding: utf-8 -*-
import os
from pathlib import Path

from pydantic import AnyHttpUrl, Field
from pydantic_settings import SettingsConfigDict

from ._base import FrozenBaseConfig


class ChallengeConfig(FrozenBaseConfig):
    base_url: AnyHttpUrl = Field(...)
    commit_dir: str = Field(
        default_factory=lambda: str(
            Path(os.environ.get("MDM_COMMIT_DIR", "/commit")).resolve()
        )
    )
    simple_bot_network_name: str = Field(default="bot-simple-network")
    challenge_network_name: str = Field(default="bot-challenge-network")
    miner_image_tag: str = Field(default="redteamsubnet/bv-miner:latest")
    simple_bot_url: AnyHttpUrl = Field(default="https://simplebot.theredteam.io")
    simple_bot_poll_max_attempts: int = Field(default=5, ge=1)
    simple_bot_poll_interval_sec: float = Field(default=2.0, ge=0)
    container_run_timeout_sec: int = Field(default=10, ge=0)

    model_config = SettingsConfigDict(env_prefix="MDM_CHALLENGE_")


__all__ = [
    "ChallengeConfig",
]
