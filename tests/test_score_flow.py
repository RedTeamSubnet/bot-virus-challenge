import threading

from fastapi.testclient import TestClient

from src.bv_challenge.challenge.api.main import app
from api.config import config
from api.endpoints.challenge import service
from api.endpoints.challenge.payload_manager import SessionRecord
from api.endpoints.challenge.schemas import MinerOutput

client = TestClient(app)


class _FakePayloadManager:
    run_id = "test-run"

    def __init__(
        self, hard_fail: SessionRecord | None = None, completed: int = 1
    ) -> None:
        self.run_lock = threading.Lock()
        self.hard_fail = hard_fail
        self.completed = completed
        self.resets = 0
        self.pops = 0

    def has_remaining_tasks(self) -> bool:
        return True

    def remaining_task_count(self) -> int:
        return 100

    def reset(self) -> None:
        self.resets += 1

    def pop_task(self) -> object:
        self.pops += 1
        return object()

    def first_hard_fail(self):
        return self.hard_fail

    def completed_count(self) -> int:
        return self.completed

    def fallback_count(self) -> int:
        return 0

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


def _patch(monkeypatch, manager, web_results=(False, False)):
    calls = []
    results = list(web_results)
    monkeypatch.setattr(service, "payload_manager", manager)
    monkeypatch.setattr(
        service.challenge_utils,
        "send_build_request",
        lambda *args: calls.append(("build",)),
    )

    def _web(mgr, **kwargs):
        calls.append(("web", kwargs))
        return results.pop(0)

    monkeypatch.setattr(service.challenge_utils, "run_web_phase", _web)
    return calls


def test_score_runs_smoke_session_then_the_remaining_sessions(monkeypatch) -> None:
    n = config.challenge.n_run_per_ch
    manager = _FakePayloadManager()
    calls = _patch(monkeypatch, manager)

    assert service.score(miner_output=_miner_output()) == 0.8

    assert calls[0] == ("build",)
    assert [c[1]["session_count"] for c in calls[1:]] == [1, n - 1]
    assert "target_completed" not in calls[1][1]
    assert calls[2][1]["target_completed"] == n
    assert manager.pops == 1  # keys are popped once; batch 2 continues from it

    result = service.get_result()
    assert result["phase"] == "web"
    assert result["gate_passed"] is True
    assert result["gate_reason"] is None
    assert "simple_bot_passed" not in result


def test_smoke_hard_fail_stops_the_run_with_zero(monkeypatch) -> None:
    failed = SessionRecord(
        session_id="s", completed=True, hard_fail=True, hard_fail_reason="automation"
    )
    calls = _patch(monkeypatch, _FakePayloadManager(failed))

    assert service.score(miner_output=_miner_output()) == 0.0

    assert [c[0] for c in calls] == ["build", "web"]  # no second batch
    result = service.get_result()
    assert result["score"] == 0.0
    assert result["phase"] == "gate"
    assert result["gate_passed"] is False
    assert result["gate_reason"] == "automation"
    assert result["category_scores"] == {}


def test_smoke_task_failure_reports_task_reason(monkeypatch) -> None:
    failed = SessionRecord(
        session_id="s", completed=True, hard_fail=True, hard_fail_reason="task"
    )
    _patch(monkeypatch, _FakePayloadManager(failed))

    assert service.score(miner_output=_miner_output()) == 0.0
    assert service.get_result()["gate_reason"] == "task"


def test_smoke_session_that_never_arrives_fails_the_gate(monkeypatch) -> None:
    calls = _patch(monkeypatch, _FakePayloadManager(completed=0))

    assert service.score(miner_output=_miner_output()) == 0.0

    assert [c[0] for c in calls] == ["build", "web"]  # no second batch
    result = service.get_result()
    assert result["phase"] == "gate"
    assert result["gate_passed"] is False
    assert result["gate_reason"] == "timeout"


def test_runner_failure_in_smoke_skips_second_batch(monkeypatch) -> None:
    calls = _patch(monkeypatch, _FakePayloadManager(), web_results=(True,))

    service.score(miner_output=_miner_output())

    assert [c[0] for c in calls] == ["build", "web"]
    result = service.get_result()
    assert result["phase"] == "web"
    assert result["feedback"] == "failed in web scoring phase"
    assert result["gate_passed"] is None


def test_runner_failure_in_second_batch_keeps_gate_passed(monkeypatch) -> None:
    _patch(monkeypatch, _FakePayloadManager(), web_results=(False, True))

    service.score(miner_output=_miner_output())

    result = service.get_result()
    assert result["feedback"] == "failed in web scoring phase"
    assert result["gate_passed"] is True


def test_score_returns_zero_when_build_fails(monkeypatch) -> None:
    def _fail_build(*args):
        raise RuntimeError("build failed")

    monkeypatch.setattr(service, "payload_manager", _FakePayloadManager())
    monkeypatch.setattr(service.challenge_utils, "send_build_request", _fail_build)

    assert service.score(miner_output=_miner_output()) == 0.0
    assert service.get_result()["phase"] == "build"
    assert service.get_result()["gate_passed"] is None


def test_result_endpoint_exposes_gate_fields(monkeypatch) -> None:
    monkeypatch.setattr(service, "_latest_result", dict(service._latest_result))

    service._set_latest_result(
        score=0.0, feedback="x", phase="gate", gate_passed=False, gate_reason="integrity"
    )
    body = client.get("/result").json()
    assert body["gate_passed"] is False
    assert body["gate_reason"] == "integrity"

    service._set_latest_result(score=0.5, feedback="x", phase="web")
    body = client.get("/result").json()
    assert body["gate_passed"] is None
    assert body["gate_reason"] is None


def test_result_endpoint_has_gate_fields_before_any_run() -> None:
    assert {"gate_passed", "gate_reason"} <= set(client.get("/result").json())
