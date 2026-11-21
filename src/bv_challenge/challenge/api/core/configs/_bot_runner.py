# -*- coding: utf-8 -*-

from pydantic import Field, SecretStr
from pydantic_settings import SettingsConfigDict

from api.core.constants import ENV_PREFIX
from ._base import FrozenBaseConfig


class BotRunnerConfig(FrozenBaseConfig):
    endpoint: str = Field(...)
    timeout: int = Field(default=120, ge=1)
    ssl_verify: bool = Field(default=True)
    key: SecretStr = Field(..., min_length=9, max_length=128)

    model_config = SettingsConfigDict(env_prefix=f"{ENV_PREFIX}BOT_RUNNER_")


__all__ = ["BotRunnerConfig"]
