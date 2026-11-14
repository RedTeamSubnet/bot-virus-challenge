# -*- coding: utf-8 -*-

import os
from pathlib import Path
from typing import Dict, Optional

from pydantic import BaseModel, Field, confloat, constr, field_validator

from api.core.constants import ALPHANUM_REGEX


_COMMIT_DIR = Path(
    os.getenv(
        "BV_CHALLENGE_API_DIR",
        str(Path(__file__).resolve().parents[3]),
    )
) / "commit"
_BOT_DIR = _COMMIT_DIR / "bot"


def _read_example(file_name: str) -> str:
    try:
        return (_BOT_DIR / file_name).read_text()
    except OSError:
        return ""


_BOT_PY_EXAMPLE = _read_example("bot.py")
_DOCKERFILE_EXAMPLE = _read_example("Dockerfile")


class CommitFilePM(BaseModel):
    file_name: constr(strip_whitespace=True, min_length=4, max_length=64) = Field(  # type: ignore
        ...,
        title="File Name",
        description="Miner file name.",
        examples=["bot.py", "Dockerfile"],
    )
    content: constr(strip_whitespace=True, min_length=2) = Field(  # type: ignore
        ...,
        title="File Content",
        description="Content of the file as a string.",
        examples=[_BOT_PY_EXAMPLE, _DOCKERFILE_EXAMPLE],
    )

    @field_validator("file_name")
    @classmethod
    def _check_file_name(cls, val: str) -> str:
        if val not in {"bot.py", "Dockerfile"}:
            raise ValueError("Only bot.py and Dockerfile are allowed")
        return val


class MinerOutput(BaseModel):
    commit_files: list[CommitFilePM] = Field(
        ...,
        min_length=2,
        max_length=2,
        title="Commit Files",
        description="Exactly bot.py and Dockerfile.",
    )
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "commit_files": [
                        {"file_name": "bot.py", "content": _BOT_PY_EXAMPLE},
                        {
                            "file_name": "Dockerfile",
                            "content": _DOCKERFILE_EXAMPLE,
                        },
                    ]
                }
            ]
        }
    }

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
        return next(
            item.content for item in self.commit_files if item.file_name == file_name
        )


class PayloadRequest(BaseModel):
    session_id: constr(strip_whitespace=True, min_length=8, max_length=64, pattern=ALPHANUM_REGEX) = Field(  # type: ignore
        ...,
        title="Session ID",
        description="Session identifier the scorer received for the run.",
    )
    score: float = Field(..., ge=0.0, le=1.0, title="Score")
    # Finite values only: NaN/Infinity cannot be serialized by GET /result.
    category_scores: Dict[str, confloat(allow_inf_nan=False)] = Field(  # type: ignore
        default_factory=dict,
        title="Category Scores",
        description="Per-category breakdown of the score; passed through to /result.",
    )
    feedback: Optional[str] = Field(
        default=None,
        max_length=512,
        title="Feedback",
        description="Optional human-readable feedback from the scorer.",
    )


class ScoreResponse(BaseModel):
    session_id: str = Field(..., title="Session ID")
    score: float = Field(..., ge=0.0, le=1.0, title="Score")


__all__ = [
    "CommitFilePM",
    "MinerOutput",
    "PayloadRequest",
    "ScoreResponse",
]
