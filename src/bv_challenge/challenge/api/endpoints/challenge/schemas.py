# -*- coding: utf-8 -*-

import os
from typing import Optional, Union, List, Dict, Any

from pydantic import BaseModel, Field, constr, field_validator

from api.core.constants import (
    ALPHANUM_REGEX,
    ALPHANUM_HOST_REGEX,
    ALPHANUM_EXTEND_REGEX,
    REQUIREMENTS_REGEX,
    ALPHANUM_CUSTOM_REGEX,
)
from api.config import config
from api.core import utils

_api_dir = os.getenv("BV_CHALLENGE_API_DIR", "/app/rest-bv-challenge")
_bot_dir = os.path.join(_api_dir, "bot")

_bot_py_content = ""
_bot_py_path = os.path.join(_bot_dir, "bot.py")
if os.path.exists(_bot_py_path):
    with open(_bot_py_path, "r") as _bot_py_file:
        _bot_py_content = _bot_py_file.read()

_dockerfile_content = ""
_dockerfile_path = os.path.join(_bot_dir, "Dockerfile")
if os.path.exists(_dockerfile_path):
    with open(_dockerfile_path, "r") as _dockerfile_file:
        _dockerfile_content = _dockerfile_file.read()


class KeyPairPM(BaseModel):
    private_key: str = Field(
        ...,
        min_length=32,
        title="Private Key",
        description="Private key as a string.",
    )
    public_key: Union[str, None] = Field(
        ...,
        min_length=32,
        title="Public Key",
        description="Public key as a string.",
    )
    nonce: Union[
        constr(
            strip_whitespace=True,
            min_length=4,
            max_length=64,
            pattern=ALPHANUM_REGEX,
        ),  # type: ignore
        None,
    ] = Field(
        ...,
        title="Nonce",
        description="Random value to prevent caching.",
        examples=["a1b2c3d4e5f6g7h8"],
    )


class MinerFilePM(BaseModel):
    fname: constr(strip_whitespace=True) = Field(  # type: ignore
        ...,
        min_length=4,
        max_length=64,
        pattern=ALPHANUM_HOST_REGEX,
        title="File Name",
        description="Name of the file.",
        examples=["config.py"],
    )
    content: constr(strip_whitespace=True) = Field(  # type: ignore
        ...,
        min_length=2,
        title="File Content",
        description="Content of the file as a string.",
        examples=["threshold = 0.5"],
    )

    @field_validator("fname")
    @classmethod
    def _check_fname(cls, val: str) -> str:

        if not isinstance(val, str):
            raise TypeError("File name must be a string!")

        if val.startswith("."):
            raise ValueError("File name cannot start with a dot(.)!")

        _allowed_exts = config.challenge.allowed_file_exts
        if not val.endswith(tuple(_allowed_exts)):
            raise ValueError(
                f"File extension is not supported, only '{_allowed_exts}' extensions are allowed!"
            )

        return val


class MinerInput(BaseModel):
    random_val: Optional[
        constr(
            strip_whitespace=True, min_length=4, max_length=64, pattern=ALPHANUM_REGEX
        )  # type: ignore
    ] = Field(
        default_factory=utils.gen_random_string,
        title="Random Value",
        description="Random value to prevent caching.",
        examples=["a1b2c3d4e5f6g7h8"],
    )


class MinerOutput(BaseModel):
    bot_py: str = Field(
        ...,
        title="bot.py",
        min_length=2,
        description="The main bot.py source code for the challenge.",
        examples=[_bot_py_content],
    )
    dockerfile: str = Field(
        ...,
        title="Dockerfile",
        min_length=2,
        description="Dockerfile to build the bot container",
        examples=[_dockerfile_content],
    )
    score_job_id: str = Field(
        default="",
        max_length=128,
        description=(
            "Optional caller-supplied job ID. Forwarded to vm-runner so the bot "
            "container is named bot_container_<id> and labeled score_job_id=<id>, "
            "enabling external log streaming."
        ),
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
                "Dockerfile content is too long, max 500 lines are allowed!"
            )
        return val


class ErrorData(BaseModel):
    data: str = Field(
        ...,
        min_length=2,
        pattern=ALPHANUM_CUSTOM_REGEX,
        description="Bot data to evaluate.",
        examples=["data"],
    )


class EvalPayload(BaseModel):
    error: ErrorData


class RandomValRequest(BaseModel):
    random_val: str = Field(
        ...,
        min_length=4,
        max_length=64,
        pattern=ALPHANUM_REGEX,
        title="Random value",
        description="Random value.",
        examples=["a1b2c3d4e5f6g7h8"],
    )


__all__ = ["KeyPairPM", "MinerInput", "MinerOutput", "EvalPayload", "RandomValRequest"]
