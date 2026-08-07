# -*- coding: utf-8 -*-

from pydantic import BaseModel, Field, field_validator


class BuildRequest(BaseModel):
    bot_py: str = Field(
        ...,
        title="bot.py",
        min_length=2,
        description="The bot.py source code to be executed.",
    )
    dockerfile: str = Field(
        ...,
        title="Dockerfile",
        min_length=2,
        description="Dockerfile content for building the bot container.",
    )
    score_job_id: str = Field(
        default="",
        max_length=128,
        description="Optional caller-supplied job ID used for container names and labels.",
    )

    @field_validator("bot_py", mode="after")
    @classmethod
    def _check_bot_py_lines(cls, val: str) -> str:
        _lines = val.split("\n")
        if len(_lines) > 2000:
            raise ValueError("bot_py content is too long, max 2000 lines are allowed!")
        return val

    @field_validator("dockerfile", mode="after")
    @classmethod
    def _check_dockerfile_lines(cls, val: str) -> str:
        _lines = val.split("\n")
        if len(_lines) > 500:
            raise ValueError(
                "dockerfile content is too long, max 500 lines are allowed!"
            )
        return val


class RunSimpleBotRequest(BaseModel):
    score_job_id: str = Field(
        default="",
        max_length=128,
        description="Optional caller-supplied job ID used for container names and labels.",
    )
    timeout_sec: int = Field(
        default=60,
        ge=1,
        le=3600,
        description="Seconds to wait for simple bot backend result after miner exits.",
    )


class RunWebRequest(BaseModel):
    session_count: int = Field(
        default=2,
        ge=1,
        le=10,
        description="Number of challenge web sessions to run the bot.",
    )
    score_job_id: str = Field(
        default="",
        max_length=128,
        description="Optional caller-supplied job ID used for container names and labels.",
    )


__all__ = ["BuildRequest", "RunSimpleBotRequest", "RunWebRequest"]
