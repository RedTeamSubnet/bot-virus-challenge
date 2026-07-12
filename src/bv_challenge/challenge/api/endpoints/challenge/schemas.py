# -*- coding: utf-8 -*-

from typing import Optional, Union

from pydantic import BaseModel, Field, constr, field_validator

from api.core import utils
from api.core.constants import ALPHANUM_CUSTOM_REGEX, ALPHANUM_REGEX


class KeyPairPM(BaseModel):
    private_key: str = Field(..., min_length=32, title="Private Key")
    public_key: Union[str, None] = Field(default=None, title="Public Key")
    nonce: Union[
        constr(  # type: ignore
            strip_whitespace=True,
            min_length=4,
            max_length=64,
            pattern=ALPHANUM_REGEX,
        ),
        None,
    ] = Field(default=None, title="Nonce")


class CommitFilePM(BaseModel):
    file_name: constr(strip_whitespace=True, min_length=4, max_length=64) = Field(  # type: ignore
        ...,
        title="File Name",
        description="Miner file name.",
        examples=["bot.py"],
    )
    content: constr(strip_whitespace=True, min_length=2) = Field(  # type: ignore
        ...,
        title="File Content",
        description="Content of the file as a string.",
        examples=["print('hello')"],
    )

    @field_validator("file_name")
    @classmethod
    def _check_file_name(cls, val: str) -> str:
        if val not in {"bot.py", "Dockerfile"}:
            raise ValueError("Only bot.py and Dockerfile are allowed")
        return val


class MinerInput(BaseModel):
    random_val: Optional[
        constr(  # type: ignore
            strip_whitespace=True,
            min_length=4,
            max_length=64,
            pattern=ALPHANUM_REGEX,
        )
    ] = Field(
        default_factory=utils.gen_random_string,
        title="Random Value",
        description="Random value to prevent caching.",
        examples=["a1b2c3d4e5f6g7h8"],
    )


class MinerOutput(BaseModel):
    commit_files: list[CommitFilePM] = Field(
        ...,
        min_length=2,
        max_length=2,
        title="Commit Files",
        description="Exactly bot.py and Dockerfile.",
    )

    @field_validator("commit_files", mode="after")
    @classmethod
    def _check_commit_files(cls, val: list[CommitFilePM]) -> list[CommitFilePM]:
        names = [item.file_name for item in val]
        if set(names) != {"bot.py", "Dockerfile"}:
            raise ValueError("commit_files must contain bot.py and Dockerfile")
        for item in val:
            max_lines = 2000 if item.file_name == "bot.py" else 500
            if len(item.content.splitlines()) > max_lines:
                raise ValueError(
                    f"{item.file_name} content is too long, max {max_lines} lines are allowed"
                )
        return val

    def get_file(self, file_name: str) -> str:
        return next(item.content for item in self.commit_files if item.file_name == file_name)


class ErrorData(BaseModel):
    data: str = Field(
        ...,
        min_length=2,
        max_length=64,
        pattern=ALPHANUM_CUSTOM_REGEX,
        title="Bot Data",
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


__all__ = [
    "KeyPairPM",
    "CommitFilePM",
    "MinerInput",
    "MinerOutput",
    "EvalPayload",
    "RandomValRequest",
]
