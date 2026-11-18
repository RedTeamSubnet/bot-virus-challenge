# -*- coding: utf-8 -*-

from pydantic import Field, SecretStr
from pydantic_settings import SettingsConfigDict

from api.core.constants import ENV_PREFIX
from ._base import FrozenBaseConfig


class ChallengeConfig(FrozenBaseConfig):
    bot_timeout: int = Field(default=120, ge=1)
    session_timeout_score: float = Field(default=0.0, ge=0.0, le=1.0)
    bot_runner_endpoint: str = Field(...)
    bot_runner_timeout: int = Field(default=120, ge=1)
    bot_runner_ssl_verify: bool = Field(default=True)
    # Sent as X-API-Key to the bot-runner VM (protects /build and /run-web).
    # min_length=9: bot-runner's auth_api_key rejects len <= 8.
    bot_runner_key: SecretStr = Field(..., min_length=9, max_length=128)
    api_key: SecretStr = Field(..., min_length=8, max_length=128)

    model_config = SettingsConfigDict(env_prefix=f"{ENV_PREFIX}CHALLENGE_")


__all__ = ["ChallengeConfig"]
