import threading

from src.bv_challenge.challenge.api.main import app  # noqa: F401  (sets up `api` imports)
from api.endpoints.challenge import service
from api.endpoints.challenge.schemas import MinerOutput


class _FakePayloadManager:
    run_id = "test-run"

    def __init__(self) -> None:
        self.run_lock = threading.Lock()

    def has_remaining_tasks(self) -> bool:
        return True

    def remaining_task_count(self) -> int:
        return 100

    def reset(self) -> None:
        pass

    def pop_task(self) -> object:
        return object()

    def finalize(self, timeout_score: float) -> float:
        return 0.8

    def finalize_category_scores(self) -> dict:
        return {"session": 0.9}


def _miner_output() -> MinerOutput:
    return MinerOutput(
        commit_files=[
            {"file_name": "bot.py", "content": "print('bot')"},
            {"file_name": "Dockerfile", "content": "FROM python:3.12-slim"},
        ]
    )


def test_score_goes_from_build_straight_to_web_phase(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(service, "payload_manager", _FakePayloadManager())
    monkeypatch.setattr(
        service.challenge_utils,
        "send_build_request",
        lambda *args: calls.append("build"),
    )
    monkeypatch.setattr(
        service.challenge_utils,
        "run_web_phase",
        lambda *args, **kwargs: calls.append("web") or False,
    )

    assert service.score(miner_output=_miner_output()) == 0.8
    assert calls == ["build", "web"]

    result = service.get_result()
    assert result["phase"] == "web"
    assert "simple_bot_passed" not in result


def test_score_returns_zero_when_build_fails(monkeypatch) -> None:
    def _fail_build(*args):
        raise RuntimeError("build failed")

    monkeypatch.setattr(service, "payload_manager", _FakePayloadManager())
    monkeypatch.setattr(service.challenge_utils, "send_build_request", _fail_build)

    assert service.score(miner_output=_miner_output()) == 0.0
    assert service.get_result()["phase"] == "build"
