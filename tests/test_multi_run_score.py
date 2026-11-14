"""One submission = one build + N scored sessions (bt-2 flow)."""

from types import SimpleNamespace

import pytest

from src.bv_challenge.challenge.api.main import app  # noqa: F401  (loads config)
from api.config import config
from api.endpoints.challenge import service
from api.endpoints.challenge.schemas import MinerOutput, PayloadRequest

MINER = MinerOutput(
    commit_files=[
        {"file_name": "bot.py", "content": "def run_bot(url):\n    return True\n"},
        {"file_name": "Dockerfile", "content": "FROM python:3.11-slim\n"},
    ]
)


def _challenge(**over):
    base = dict(
        runs_per_submission=8,
        reject_below=0.5,
        score_aggregation="min",
        bot_timeout=1,
        session_timeout_score=0.0,
        scorer_endpoint=None,
        feedback_detail="minimal",
        session_retries=0,
        runs_compare_min=3,
    )
    base.update(over)
    return SimpleNamespace(challenge=SimpleNamespace(**base))


@pytest.fixture
def runner(monkeypatch):
    """Fake bot-runner: each run posts the next scripted score (or fails)."""
    state = {"builds": 0, "runs": [], "script": [], "build_fails": False}

    def build(bot_py, dockerfile, session_id):
        state["builds"] += 1
        if state["build_fails"]:
            raise ValueError("build failed")
        return {"status": "success"}

    def run_web(session_id, keep_image=False):
        state["runs"].append(keep_image)
        step = state["script"][len(state["runs"]) - 1]
        if step == "runner_error":
            raise ValueError("bot container exited with status 1")
        if step == "timeout":
            return {"status": "success"}
        service.record_payload(
            PayloadRequest(
                session_id=session_id,
                score=step,
                category_scores={"activity": step},
                feedback=f"session={step}",
            )
        )
        return {"status": "success"}

    monkeypatch.setattr(service.challenge_utils, "send_build_request", build)
    monkeypatch.setattr(service.challenge_utils, "send_run_web_request", run_web)
    return state


def _use(monkeypatch, **over):
    monkeypatch.setattr(service, "config", _challenge(**over))


def test_yaml_runs_ten_sessions_and_takes_the_worst() -> None:
    assert config.challenge.runs_per_submission == 10
    assert config.challenge.reject_below == 0.5
    assert config.challenge.score_aggregation == "min"


def test_all_sessions_pass_the_worst_session_decides(monkeypatch, runner) -> None:
    _use(monkeypatch)
    runner["script"] = [0.95, 0.93, 0.91, 0.96, 0.88, 0.94, 0.92, 0.95]
    out = service.score(MINER)
    assert out.score == pytest.approx(0.88)
    assert runner["builds"] == 1
    # Image kept for runs 1..7, cleaned after the last.
    assert runner["runs"] == [True] * 7 + [False]
    res = service._full_result()
    assert (
        res["phase"] == "completed" and res["runs"] == 8 and len(res["sessions"]) == 8
    )
    assert res["category_scores"] == {"activity": 0.88}
    assert out.session_id == res["session_id"]


def test_a_detected_session_rejects_the_submission_and_stops(
    monkeypatch, runner
) -> None:
    _use(monkeypatch)
    runner["script"] = [0.95, 0.94, 0.45, 0.95, 0.95, 0.95, 0.95, 0.95]
    out = service.score(MINER)
    assert out.score == 0.0
    assert len(runner["runs"]) == 3
    res = service._full_result()
    assert res["phase"] == "rejected"
    assert "session 3/8" in res["feedback"] and "0.450" in res["feedback"]
    assert [s["score"] for s in res["sessions"]] == [0.95, 0.94, 0.45]


def test_a_hard_fail_on_the_first_session_skips_every_other_run(
    monkeypatch, runner
) -> None:
    _use(monkeypatch)
    runner["script"] = [0.0] * 8
    assert service.score(MINER).score == 0.0
    assert runner["runs"] == [True]


def test_a_timed_out_session_rejects(monkeypatch, runner) -> None:
    _use(monkeypatch)
    runner["script"] = [0.95, "timeout"] + [0.95] * 6
    assert service.score(MINER).score == 0.0
    res = service._full_result()
    assert res["phase"] == "timeout" and len(runner["runs"]) == 2


def test_a_runner_failure_mid_submission_rejects(monkeypatch, runner) -> None:
    _use(monkeypatch)
    runner["script"] = [0.95, 0.95, "runner_error"] + [0.95] * 5
    assert service.score(MINER).score == 0.0
    assert service._full_result()["phase"] == "run"


def test_build_failure_runs_nothing(monkeypatch, runner) -> None:
    _use(monkeypatch)
    runner["build_fails"] = True
    assert service.score(MINER).score == 0.0
    assert runner["runs"] == [] and service._full_result()["phase"] == "build"


def test_mean_aggregation_is_available(monkeypatch, runner) -> None:
    _use(monkeypatch, runs_per_submission=4, score_aggregation="mean")
    runner["script"] = [0.9, 0.8, 1.0, 0.7]
    assert service.score(MINER).score == pytest.approx(0.85)


def test_one_run_keeps_the_old_single_session_behaviour(monkeypatch, runner) -> None:
    _use(monkeypatch, runs_per_submission=1)
    runner["script"] = [0.91]
    out = service.score(MINER)
    assert out.score == pytest.approx(0.91)
    assert runner["runs"] == [False]


def test_sessions_are_unregistered_after_the_run(monkeypatch, runner) -> None:
    _use(monkeypatch)
    runner["script"] = [0.95] * 8
    service.score(MINER)
    assert service._pending_sessions == {}


# ---- cross-run comparison by the scorer ------------------------------------


@pytest.fixture
def scorer(monkeypatch):
    state = {
        "calls": [],
        "verdict": {"level": "no_significant_findings", "score_cap": None},
        "exc": None,
    }

    def compare(session_ids):
        state["calls"].append(list(session_ids))
        if state["exc"] is not None:
            raise state["exc"]
        return {"runs": len(session_ids), "missing": 0, **state["verdict"]}

    monkeypatch.setattr(service.challenge_utils, "compare_runs", compare)
    return state


def test_sessions_that_pass_alone_but_show_automation_together_are_rejected(
    monkeypatch, runner, scorer
) -> None:
    _use(monkeypatch, scorer_endpoint="http://scorer:8000")
    runner["script"] = [0.93] * 8
    scorer["verdict"] = {"level": "automation_supported", "score_cap": 0.45}
    assert service.score(MINER).score == 0.0
    res = service._full_result()
    assert res["phase"] == "rejected" and "together show automation" in res["feedback"]
    assert len(scorer["calls"]) == 1 and len(scorer["calls"][0]) == 8
    assert scorer["calls"][0] == [s["session_id"] for s in res["sessions"]]


def test_clean_comparison_keeps_the_worst_session_score(
    monkeypatch, runner, scorer
) -> None:
    _use(monkeypatch, scorer_endpoint="http://scorer:8000")
    runner["script"] = [0.95, 0.9, 0.93, 0.97, 0.91, 0.94, 0.96, 0.92]
    assert service.score(MINER).score == pytest.approx(0.9)
    assert service._full_result()["phase"] == "completed"


def test_a_cap_above_the_reject_line_only_caps(monkeypatch, runner, scorer) -> None:
    _use(monkeypatch, scorer_endpoint="http://scorer:8000", reject_below=0.3)
    runner["script"] = [0.93] * 8
    scorer["verdict"] = {"level": "automation_supported", "score_cap": 0.45}
    assert service.score(MINER).score == pytest.approx(0.45)


def test_rejected_sessions_are_never_sent_for_comparison(
    monkeypatch, runner, scorer
) -> None:
    _use(monkeypatch, scorer_endpoint="http://scorer:8000")
    runner["script"] = [0.95, 0.45] + [0.95] * 6
    assert service.score(MINER).score == 0.0
    assert scorer["calls"] == []


def test_comparison_is_skipped_without_a_scorer_or_with_too_few_runs(
    monkeypatch, runner, scorer
) -> None:
    _use(monkeypatch)
    runner["script"] = [0.93] * 8
    assert service.score(MINER).score == pytest.approx(0.93)
    _use(monkeypatch, scorer_endpoint="http://scorer:8000", runs_per_submission=2)
    runner["runs"].clear()
    assert service.score(MINER).score == pytest.approx(0.93)
    assert scorer["calls"] == []


def test_a_failed_comparison_is_an_error_not_a_score(
    monkeypatch, runner, scorer
) -> None:
    from api.core.exceptions import BaseHTTPException

    _use(monkeypatch, scorer_endpoint="http://scorer:8000")
    runner["script"] = [0.93] * 8
    scorer["exc"] = ValueError("scorer run comparison returned 503")
    with pytest.raises(BaseHTTPException) as err:
        service.score(MINER)
    assert err.value.status_code == 503
    assert service._full_result()["phase"] == "infrastructure_error"


@pytest.mark.parametrize(
    "status, data",
    [
        (503, None),
        (200, {"runs": 7, "missing": 1, "level": "suspicious", "score_cap": None}),
        (
            200,
            {
                "runs": 8,
                "missing": 0,
                "level": "automation_supported",
                "score_cap": 4.5,
            },
        ),
        (200, {"runs": 8, "missing": 0}),
        (200, None),
    ],
)
def test_compare_runs_refuses_untrustworthy_scorer_answers(
    monkeypatch, status, data
) -> None:
    from api.endpoints.challenge import utils

    class _Resp:
        status_code = status

        @staticmethod
        def json():
            return {"data": data}

    monkeypatch.setattr(
        utils,
        "config",
        SimpleNamespace(
            challenge=SimpleNamespace(
                scorer_endpoint="http://scorer:8000",
                scorer_timeout=5,
                scorer_ssl_verify=True,
                api_key=SimpleNamespace(get_secret_value=lambda: "k" * 12),
            )
        ),
    )
    monkeypatch.setattr(utils.requests, "post", lambda *a, **k: _Resp())
    with pytest.raises(ValueError):
        utils.compare_runs([f"session{i:08d}" for i in range(8)])


# ---- what miners see --------------------------------------------------------


def test_result_hides_detection_details_by_default(monkeypatch, runner) -> None:
    _use(monkeypatch)
    runner["script"] = [0.95, 0.94, 0.45] + [0.95] * 5
    out = service.score(MINER)
    public = service.get_result()
    assert public == {
        "session_id": None,
        "score": 0.0,
        "feedback": "rejected",
        "phase": "rejected",
        "category_scores": {},
        "runs": 8,
        "sessions": [],
    }
    # Nothing in the public result names a session or a score that was seen.
    assert out.session_id not in str(public) and "0.45" not in str(public)
    assert len(service._full_result()["sessions"]) == 3


def test_passing_result_shows_only_the_final_score(monkeypatch, runner) -> None:
    _use(monkeypatch)
    runner["script"] = [0.95, 0.88] + [0.95] * 6
    service.score(MINER)
    public = service.get_result()
    assert public["score"] == pytest.approx(0.88) and public["feedback"] == "scored"
    assert public["category_scores"] == {} and public["sessions"] == []


def test_build_and_timeout_failures_stay_visible(monkeypatch, runner) -> None:
    _use(monkeypatch)
    runner["build_fails"] = True
    service.score(MINER)
    assert service.get_result()["feedback"] == "failed to build miner container"
    runner["build_fails"] = False
    runner["script"] = ["timeout"]
    service.score(MINER)
    assert service.get_result()["phase"] == "timeout"


def test_full_detail_is_opt_in(monkeypatch, runner) -> None:
    _use(monkeypatch, feedback_detail="full")
    runner["script"] = [0.95] * 8
    service.score(MINER)
    assert len(service.get_result()["sessions"]) == 8


# ---- infrastructure retry and the scoring lock ------------------------------


def test_one_timed_out_session_is_rerun_and_the_submission_still_scores(
    monkeypatch, runner
) -> None:
    _use(monkeypatch, session_retries=1)
    runner["script"] = [0.95, "timeout", 0.9] + [0.95] * 6
    assert service.score(MINER).score == pytest.approx(0.9)
    assert len(runner["runs"]) == 9
    full = service._full_result()
    # The timed-out attempt is not one of the 8 scored sessions.
    assert len(full["sessions"]) == 8 and all(
        s["phase"] == "completed" for s in full["sessions"]
    )


def test_the_retry_budget_is_per_submission(monkeypatch, runner) -> None:
    _use(monkeypatch, session_retries=1)
    runner["script"] = [0.95, "timeout", 0.95, "runner_error"] + [0.95] * 6
    assert service.score(MINER).score == 0.0
    assert service._full_result()["phase"] == "run"
    assert len(runner["runs"]) == 4


def test_a_scored_session_is_never_rerun(monkeypatch, runner) -> None:
    _use(monkeypatch, session_retries=1)
    runner["script"] = [0.95, 0.45] + [0.95] * 6
    assert service.score(MINER).score == 0.0
    assert len(runner["runs"]) == 2


def test_the_image_is_kept_while_a_retry_is_still_possible(monkeypatch, runner) -> None:
    _use(monkeypatch, session_retries=1, runs_per_submission=3)
    runner["script"] = [0.95, 0.95, "timeout", 0.95]
    assert service.score(MINER).score == pytest.approx(0.95)
    # Last scheduled run keeps the image (a retry may follow); the retry,
    # with no budget left, lets the runner clean up.
    assert runner["runs"] == [True, True, True, False]


def test_overlapping_submissions_are_refused(monkeypatch, runner) -> None:
    from api.core.exceptions import BaseHTTPException

    _use(monkeypatch)
    runner["script"] = [0.95] * 8
    assert service._score_lock.acquire(blocking=False)
    try:
        with pytest.raises(BaseHTTPException) as err:
            service.score(MINER)
        assert err.value.status_code == 429 and runner["builds"] == 0
    finally:
        service._score_lock.release()
    assert service.score(MINER).score == pytest.approx(0.95)
