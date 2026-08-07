from pydantic import BaseModel, Field, field_validator


class MinerInput(BaseModel):
    random_val: str | None = Field(
        default=None,
        min_length=4,
        max_length=64,
        title="Random Value",
        description="Random value to prevent caching.",
        examples=["a1b2c3d4e5f6g7h8"],
    )


class CommitFilePM(BaseModel):
    file_name: str = Field(
        ...,
        min_length=4,
        max_length=64,
        title="File Name",
        description="Name of the file.",
        examples=["bot.py", "Dockerfile"],
    )
    content: str = Field(
        ...,
        min_length=2,
        title="File Content",
        description="Content of the file as a string.",
        examples=["print('ok')"],
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


__all__ = [
    "MinerInput",
    "CommitFilePM",
    "MinerOutput",
]
