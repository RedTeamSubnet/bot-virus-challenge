# -*- coding: utf-8 -*-

from pydantic import Field, model_validator
from pydantic_settings import SettingsConfigDict

from api.core.constants import ENV_PREFIX
from ._base import FrozenBaseConfig


class ChallengeConfig(FrozenBaseConfig):
    n_run_per_ch: int = Field(...)
    bot_timeout: int = Field(..., ge=1)
    simple_bot_check_enabled: bool = Field(default=True)
    web_check_enabled: bool = Field(default=True)
    # Layer 1/2 fallback score policy (not detector secrets — just policy).
    gate_fail_score: float = Field(default=0.0, ge=0.0, le=1.0)
    metrics_processor_error_score: float = Field(default=0.5, ge=0.0, le=1.0)
    session_timeout_score: float = Field(default=0.0, ge=0.0, le=1.0)
    runner_fail_score: float = Field(default=0.0, ge=0.0, le=1.0)
    # VM configuration for remote Docker build/run
    vm_endpoint: str = Field(...)
    vm_timeout: int = Field(default=120, ge=1)
    vm_ssl_verify: bool = Field(default=True)

    @model_validator(mode="after")
    def validate_enabled_checks(self) -> "ChallengeConfig":
        if not self.simple_bot_check_enabled and not self.web_check_enabled:
            raise ValueError(
                "At least one of simple_bot_check_enabled or web_check_enabled must be enabled"
            )
        return self

    model_config = SettingsConfigDict(env_prefix=f"{ENV_PREFIX}CHALLENGE_")


__all__ = ["ChallengeConfig"]
