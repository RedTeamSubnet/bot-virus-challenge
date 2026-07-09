# -*- coding: utf-8 -*-

from datetime import datetime
from typing import List

from pydantic import Field, constr
from pydantic_settings import SettingsConfigDict

from api.core.constants import ALPHANUM_HOST_REGEX, ENV_PREFIX
from ._base import FrozenBaseConfig


class ChallengeConfig(FrozenBaseConfig):
    n_ch_per_epoch: int = Field(...)
    n_run_per_ch: int = Field(...)
    docker_ulimit: int = Field(...)
    allowed_pip_pkg_dt: datetime = Field(...)
    allowed_file_exts: List[
        constr(
            strip_whitespace=True,
            min_length=2,
            max_length=16,
            pattern=ALPHANUM_HOST_REGEX,
        )  # type: ignore
    ] = Field(..., min_length=1)
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

    model_config = SettingsConfigDict(env_prefix=f"{ENV_PREFIX}CHALLENGE_")


__all__ = ["ChallengeConfig"]
