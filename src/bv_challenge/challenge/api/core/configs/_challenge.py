# -*- coding: utf-8 -*-

from pydantic import Field, SecretStr
from pydantic_settings import SettingsConfigDict

from api.core.constants import ENV_PREFIX
from ._base import FrozenBaseConfig


class ChallengeConfig(FrozenBaseConfig):
    n_run_per_ch: int = Field(...)
    bot_timeout: int = Field(..., ge=1)
    # Layer 1/2 fallback score policy (not detector secrets — just policy).
    gate_fail_score: float = Field(default=0.0, ge=0.0, le=1.0)
    metrics_processor_error_score: float = Field(default=0.5, ge=0.0, le=1.0)
    session_timeout_score: float = Field(default=0.0, ge=0.0, le=1.0)
    runner_fail_score: float = Field(default=0.0, ge=0.0, le=1.0)
    # VM configuration for remote Docker build/run
    vm_endpoint: str = Field(...)
    vm_timeout: int = Field(default=120, ge=1)
    vm_ssl_verify: bool = Field(default=True)
    api_key: SecretStr = Field(..., min_length=8, max_length=128)

    model_config = SettingsConfigDict(env_prefix=f"{ENV_PREFIX}CHALLENGE_")


__all__ = ["ChallengeConfig"]
