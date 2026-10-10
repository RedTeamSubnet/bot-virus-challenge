"""PayloadManager: key/task lifecycle, attribution, task check, hard fails.

Replaces the former eval_runner / session_store tests (those modules no longer
exist; the same behaviour now lives in PayloadManager).
"""

import json

import pytest

from src.bv_challenge.challenge.api.main import app  # noqa: F401  (sets up `api` imports)
from api.config import config
from api.endpoints.challenge import payload_manager as pm_mod
from api.endpoints.challenge.payload_manager import PayloadManager
from api.endpoints.challenge.schemas import KeyPairPM
from api.endpoints.challenge.scoring import ScoreResult

N = 4


def _fake_key_pairs() -> list[KeyPairPM]:
    return [
        KeyPairPM(
            private_key=f"private-key-{i}".ljust(40, "x"),
            public_key=f"public-key-{i}".ljust(40, "x"),
            nonce=f"nonce{i}abcdef",
        )
        for i in range(N)
    ]


@pytest.fixture
def manager(monkeypatch) -> PayloadManager:
    monkeypatch.setattr(pm_mod.challenge_utils, "gen_key_pairs", _fake_key_pairs)
    return PayloadManager()


# "Encryption" stub: a payload for a session is "<private_key>|<json>".
def _encrypt(manager: PayloadManager, nonce: str, payload: dict) -> str:
    return f"{manager.private_keys[nonce]}|{json.dumps(payload)}"


def _decrypt(ciphertext: str, private_key: str) -> str:
    tag, sep, body = ciphertext.partition("|")
    if not sep or tag != private_key:
        raise ValueError("wrong key")
    return body


def _v3_payload(task: dict, **answer) -> dict:
    return {
        "schemaVersion": "3",
        "task": {
            "seed": task["seed"],
            "answer": {
                "code": answer.get("code", task["code"]),
                "slider": answer.get("slider", task["sliderTarget"]),
            },
            "steps": [],
            "completed": answer.get("completed", True),
        },
    }


def _eval(manager, nonce, payload, score_fn):
    return manager.process_eval(
        _encrypt(manager, nonce, payload), decrypt_fn=_decrypt, score_fn=score_fn
    )


def _const(score, cats=None):
    return lambda _payload: ScoreResult(score, cats or {})


def _ok(manager, nonce):
    """A payload with the correct answer to the task issued for ``nonce``."""
    return _v3_payload(manager.issue_task(nonce))


def _boom(_payload):
    raise AssertionError("scorer must not run")


# ------------------------------- attribution -------------------------------- #
def test_attributes_by_trial_decryption_and_records_score(manager) -> None:
    outcome = _eval(manager, "nonce1abcdef", _ok(manager, "nonce1abcdef"), _const(0.7, {"a": 0.5}))
    assert outcome.status == "recorded"
    assert outcome.session_id == "nonce1abcdef"
    assert outcome.score == 0.7
    assert outcome.hard_fail is False
    assert manager.sessions["nonce1abcdef"].score == 0.7
    assert manager.completed_count() == 1


def test_duplicate_is_ignored_and_scorer_not_invoked(manager) -> None:
    payload = _ok(manager, "nonce0abcdef")
    _eval(manager, "nonce0abcdef", payload, _const(0.8))
    outcome = _eval(manager, "nonce0abcdef", payload, _boom)
    assert outcome.status == "duplicate"
    assert manager.sessions["nonce0abcdef"].score == 0.8


def test_unattributable_and_stale_payloads_are_ignored(manager) -> None:
    outcome = manager.process_eval("garbage", decrypt_fn=_decrypt, score_fn=_boom)
    assert outcome.status == "unattributable"

    stale = f"stale-key|{json.dumps({'schemaVersion': '2'})}"
    outcome = manager.process_eval(stale, decrypt_fn=_decrypt, score_fn=_boom)
    assert outcome.status == "unattributable"
    assert manager.completed_count() == 0


def test_finalize_pads_missing_sessions_with_timeout_score(manager) -> None:
    _eval(manager, "nonce0abcdef", _ok(manager, "nonce0abcdef"), _const(1.0))
    _eval(manager, "nonce1abcdef", _ok(manager, "nonce1abcdef"), _const(0.5))
    assert manager.finalize(timeout_score=0.0) == pytest.approx(1.5 / N)
    assert manager.sessions["nonce3abcdef"].timed_out is True


def test_category_scores_average_completed_sessions(manager) -> None:
    _eval(manager, "nonce0abcdef", _ok(manager, "nonce0abcdef"), _const(1.0, {"session": 1.0}))
    _eval(manager, "nonce1abcdef", _ok(manager, "nonce1abcdef"), _const(0.5, {"session": 0.5}))
    assert manager.finalize_category_scores() == {"session": 0.75}


def test_reset_replaces_keys_sessions_and_tasks(manager) -> None:
    manager.issue_task("nonce0abcdef")
    assert "nonce0abcdef" in manager.tasks
    manager.reset()
    assert manager.tasks == {}
    assert manager.completed_count() == 0


# ---------------------------------- tasks ----------------------------------- #
def test_issue_task_stores_expected_answer_by_nonce(manager) -> None:
    task = manager.issue_task("nonce2abcdef")
    assert manager.tasks["nonce2abcdef"] == task
    assert 0 <= task["sliderTarget"] <= 100
    assert task["sliderTolerance"] == config.challenge.task_slider_tolerance
    lo, hi = config.challenge.task_card_count_min, config.challenge.task_card_count_max
    assert lo <= task["cardCount"] <= hi


def test_issue_task_does_not_store_for_unknown_nonce(manager) -> None:
    task = manager.issue_task("throwaway")
    assert task["code"]
    assert manager.tasks == {}


def test_correct_task_answer_reaches_the_scorer(manager) -> None:
    task = manager.issue_task("nonce0abcdef")
    outcome = _eval(manager, "nonce0abcdef", _v3_payload(task, slider=task["sliderTarget"]), _const(0.9))
    assert outcome.status == "recorded"
    assert outcome.score == 0.9
    assert outcome.hard_fail is False
    assert manager.first_hard_fail() is None


@pytest.mark.parametrize(
    "answer",
    [{"code": "WRONG2"}, {"slider": 1000}, {"completed": False}],
)
def test_wrong_task_answer_is_hard_fail_without_scoring(manager, answer) -> None:
    task = manager.issue_task("nonce0abcdef")
    outcome = _eval(manager, "nonce0abcdef", _v3_payload(task, **answer), _boom)
    assert outcome.status == "recorded"
    assert outcome.hard_fail is True
    assert outcome.hard_fail_reason == "task"
    assert outcome.score == config.challenge.gate_fail_score
    record = manager.first_hard_fail()
    assert record is not None and record.hard_fail_reason == "task"


def test_v3_without_issued_task_is_hard_fail(manager) -> None:
    payload = _v3_payload({"seed": "s", "code": "ABCDEF", "sliderTarget": 5})
    outcome = _eval(manager, "nonce0abcdef", payload, _boom)
    assert outcome.hard_fail_reason == "task"


def test_schema_2_downgrade_does_not_skip_task_check(manager) -> None:
    manager.issue_task("nonce0abcdef")
    outcome = _eval(manager, "nonce0abcdef", {"schemaVersion": "2"}, _boom)
    assert outcome.hard_fail is True
    assert outcome.hard_fail_reason == "task"


# ------------------------------ hard-fail flags ----------------------------- #
def test_scorer_hard_fail_is_recorded_with_reason(manager) -> None:
    result = ScoreResult(0.0, {}, True, "automation")
    outcome = _eval(manager, "nonce0abcdef", _ok(manager, "nonce0abcdef"), lambda _p: result)
    assert outcome.hard_fail is True
    assert outcome.hard_fail_reason == "automation"
    record = manager.first_hard_fail()
    assert record.session_id == "nonce0abcdef"
    assert (record.score, record.hard_fail, record.hard_fail_reason) == (0.0, True, "automation")


def test_plain_score_from_scorer_is_not_a_hard_fail(manager) -> None:
    _eval(manager, "nonce0abcdef", _ok(manager, "nonce0abcdef"), _const(0.4))
    assert manager.first_hard_fail() is None


# ------------------------- two-batch key accounting ------------------------- #
def test_smoke_then_remaining_batch_hands_out_each_key_once(manager) -> None:
    # score(): reset + pop_task, then batch 1 (1 session), then batch 2 (N-1).
    assert manager.pop_task() is not None

    claimed = []
    nonce, _key, active = manager.claim_web_key()  # smoke session
    claimed.append(nonce)
    assert active is True

    # Batch 2 continues from the key left current by the smoke claim.
    for _ in range(N - 1):
        nonce, _key, active = manager.claim_web_key()
        assert active is True
        claimed.append(nonce)

    assert len(set(claimed)) == N
    assert set(claimed) == set(manager.sessions)

    # Out of keys: a further page load is served a throwaway key.
    _nonce, _key, active = manager.claim_web_key()
    assert active is False
