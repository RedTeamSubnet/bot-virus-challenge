"""/_eval path end to end: decrypt stub -> task check -> shape -> fake scorer."""

import json

import pytest

from src.bv_challenge.challenge.api.main import app  # noqa: F401  (sets up `api` imports)
from api.endpoints.challenge import payload_manager as pm_mod
from api.endpoints.challenge import scoring, service
from api.endpoints.challenge.payload_manager import PayloadManager
from api.endpoints.challenge.schemas import KeyPairPM

_LISTS = ("movements", "clicks", "mouseDowns", "mouseUps", "keydowns", "keyups", "scroll")
_OBJECTS = (
    "browserInfo", "runtimeIntegrity", "fingerprint", "apiAvailability",
    "sessionBinding", "collectorIntegrity", "submissionContext",
    "crossRealm", "errorFingerprint", "stackTraps", "hardware", "capabilities",
)  # fmt: skip


def _keys() -> list[KeyPairPM]:
    return [
        KeyPairPM(
            private_key=f"private-key-{i}".ljust(40, "x"),
            public_key=f"public-key-{i}".ljust(40, "x"),
            nonce=f"nonce{i}abcdef",
        )
        for i in range(2)
    ]


@pytest.fixture
def manager(monkeypatch) -> PayloadManager:
    monkeypatch.setattr(pm_mod.challenge_utils, "gen_key_pairs", _keys)
    mgr = PayloadManager()
    monkeypatch.setattr(service, "payload_manager", mgr)
    monkeypatch.setattr(
        service.challenge_utils,
        "decrypt",
        lambda ciphertext, private_key: ciphertext.split("|", 1)[1]
        if ciphertext.startswith(private_key + "|")
        else (_ for _ in ()).throw(ValueError("wrong key")),
    )
    return mgr


def _payload(task: dict, **extra) -> dict:
    payload = {name: [] for name in _LISTS}
    payload.update({name: {} for name in _OBJECTS})
    payload["schemaVersion"] = "3"
    payload["task"] = {
        "seed": task["seed"],
        "answer": {"code": task["code"].lower(), "slider": task["sliderTarget"] + 2},
        "steps": [],
        "completed": True,
    }
    payload.update(extra)
    return payload


def _submit(mgr: PayloadManager, nonce: str, payload: dict) -> None:
    service.eval_bot(f"{mgr.private_keys[nonce]}|{json.dumps(payload)}")


def test_hard_fail_from_processor_is_recorded_per_session(manager, monkeypatch) -> None:
    monkeypatch.setattr(
        scoring,
        "_default_metrics_processor",
        lambda data: {
            "score": 0.8,
            "score_message": "session=0.8",
            "hard_fail": True,
            "hard_fail_reason": "realm_mismatch",
        },
    )
    task = manager.issue_task("nonce0abcdef")
    _submit(manager, "nonce0abcdef", _payload(task))

    record = manager.sessions["nonce0abcdef"]
    assert (record.completed, record.score) == (True, 0.0)
    assert (record.hard_fail, record.hard_fail_reason) == (True, "realm_mismatch")
    assert manager.first_hard_fail() is record


def test_clean_session_with_old_wheel_result_is_not_a_hard_fail(manager, monkeypatch) -> None:
    monkeypatch.setattr(
        scoring,
        "_default_metrics_processor",
        lambda data: {"score": 0.8, "score_message": "session=0.8"},
    )
    task = manager.issue_task("nonce0abcdef")
    _submit(manager, "nonce0abcdef", _payload(task))

    record = manager.sessions["nonce0abcdef"]
    assert (record.score, record.hard_fail, record.hard_fail_reason) == (0.8, False, None)
    assert record.category_scores == {"session": 0.8}


def test_wrong_task_answer_never_reaches_the_processor(manager, monkeypatch) -> None:
    def _fail(data):
        raise AssertionError("scorer must not run")

    monkeypatch.setattr(scoring, "_default_metrics_processor", _fail)
    task = manager.issue_task("nonce0abcdef")
    payload = _payload(task)
    payload["task"]["answer"]["code"] = "NOPE22"
    _submit(manager, "nonce0abcdef", payload)

    record = manager.sessions["nonce0abcdef"]
    assert (record.hard_fail, record.hard_fail_reason, record.score) == (True, "task", 0.0)


def test_malformed_v3_payload_is_hard_fail_payload(manager, monkeypatch) -> None:
    monkeypatch.setattr(
        scoring, "_default_metrics_processor", lambda data: {"score": 1.0}
    )
    task = manager.issue_task("nonce0abcdef")
    payload = _payload(task)
    del payload["hardware"]
    _submit(manager, "nonce0abcdef", payload)

    record = manager.sessions["nonce0abcdef"]
    assert (record.hard_fail, record.hard_fail_reason) == (True, "payload")
