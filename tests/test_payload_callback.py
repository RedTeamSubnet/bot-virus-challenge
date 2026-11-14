"""Scorer -> challenge-api /payload contract (bt-2 flow)."""

import json

import pytest
from fastapi.testclient import TestClient

from src.bv_challenge.challenge.api.main import app
from api.config import config
from api.endpoints.challenge import service


client = TestClient(app)
_SID = "abcdef0123456789"


@pytest.fixture()
def pending():
    _p = service._register_session(_SID)
    yield _p
    service._unregister_session(_SID)


def _post(body, key: str | None = "valid"):
    _headers = {"Content-Type": "application/json"}
    if key == "valid":
        _headers["X-API-Key"] = config.challenge.api_key.get_secret_value()
    elif key is not None:
        _headers["X-API-Key"] = key
    _raw = body if isinstance(body, str) else json.dumps(body)
    return client.post("/payload", content=_raw, headers=_headers)


def test_first_result_is_recorded(pending) -> None:
    _r = _post({"session_id": _SID, "score": 0.42, "category_scores": {"session": 1.0}, "feedback": "scored"})
    assert _r.status_code == 200, _r.text
    assert pending.event.is_set() and pending.score == 0.42


def test_second_result_cannot_overwrite_the_first(pending) -> None:
    assert _post({"session_id": _SID, "score": 0.0, "feedback": "first"}).status_code == 200
    _r = _post({"session_id": _SID, "score": 1.0, "feedback": "scored"})
    assert _r.status_code == 409, _r.text
    assert pending.score == 0.0 and pending.feedback == "first"


@pytest.mark.parametrize(
    "raw",
    [
        '{"session_id": "abcdef0123456789", "score": NaN}',
        '{"session_id": "abcdef0123456789", "score": 0.5, "category_scores": {"a": NaN}}',
        '{"session_id": "abcdef0123456789", "score": 0.5, "category_scores": {"a": Infinity}}',
    ],
    ids=["nan-score", "nan-category", "inf-category"],
)
def test_non_finite_scores_are_rejected(pending, raw) -> None:
    assert _post(raw).status_code == 422
    assert not pending.event.is_set()


def test_category_values_above_one_are_still_accepted(pending) -> None:
    # The /payload contract is Dict[str, float]: the current scorer reports
    # feature counts (e.g. movement_count) there, not only [0, 1] scores.
    _cats = {"movement_count": 120.0, "duration_ms": 4000}
    assert _post({"session_id": _SID, "score": 0.5, "category_scores": _cats}).status_code == 200
    assert pending.category_scores == _cats


@pytest.mark.parametrize("key", [None, "wrong-key-1234567"])
def test_callback_requires_the_api_key(pending, key) -> None:
    assert _post({"session_id": _SID, "score": 1.0}, key=key).status_code == 401
    assert not pending.event.is_set()


def test_unknown_session_is_404() -> None:
    assert _post({"session_id": "zzzzzz0123456789", "score": 1.0}).status_code == 404


def test_score_replies_with_a_bare_number(monkeypatch) -> None:
    # The validator reads the /score body as a float; an object counts as 0.0.
    def _build(bot_py, dockerfile, session_id):
        raise ValueError("build failed")

    monkeypatch.setattr(service.challenge_utils, "send_build_request", _build)
    _files = [
        {"file_name": "bot.py", "content": "def run_bot(url):\n    return True\n"},
        {"file_name": "Dockerfile", "content": "FROM python:3.11-slim\n"},
    ]
    _r = client.post(
        "/score",
        json={"commit_files": _files},
        headers={"X-API-Key": config.challenge.api_key.get_secret_value()},
    )
    assert _r.status_code == 200 and _r.json() == 0.0
    assert isinstance(_r.json(), float)
