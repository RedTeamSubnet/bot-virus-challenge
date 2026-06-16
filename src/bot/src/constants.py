# -*- coding: utf-8 -*-
from pydantic import Field
from pydantic_settings import SettingsConfigDict, BaseSettings
ENV_PREFIX = "BV_"
class Config(BaseSettings):
    WEB_URL: str = Field("http://challenge-api:10001/_web")
    SESSION_COUNT: int = Field(2)

    model_config = SettingsConfigDict(
        # validate_assignment=True,
        env_file=".env",
        env_prefix=ENV_PREFIX,
        env_nested_delimiter="__",
        extra="ignore",
    )

__all__ = ["Config", "ENV_PREFIX"]
