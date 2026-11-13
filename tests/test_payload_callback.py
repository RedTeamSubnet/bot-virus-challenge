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
    assert _post({"session_id": _SID, "score": 0.0, "feedback": "hard_fail=automation"}).status_code == 200
    _r = _post({"session_id": _SID, "score": 1.0, "feedback": "scored"})
    assert _r.status_code == 409, _r.text
    assert pending.score == 0.0 and pending.feedback == "hard_fail=automation"


@pytest.mark.parametrize(
    "raw",
    [
        '{"session_id": "abcdef0123456789", "score": NaN}',
        '{"session_id": "abcdef0123456789", "score": 0.5, "category_scores": {"a": NaN}}',
        '{"session_id": "abcdef0123456789", "score": 0.5, "category_scores": {"a": Infinity}}',
        '{"session_id": "abcdef0123456789", "score": 0.5, "category_scores": {"a": 1.5}}',
        '{"session_id": "abcdef0123456789", "score": 0.5, "category_scores": {"": 0.5}}',
    ],
    ids=["nan-score", "nan-category", "inf-category", "category-above-1", "empty-category-name"],
)
def test_non_finite_or_out_of_range_scores_are_rejected(pending, raw) -> None:
    assert _post(raw).status_code == 422
    assert not pending.event.is_set()


def test_too_many_categories_are_rejected(pending) -> None:
    _cats = {f"c{i}": 0.5 for i in range(17)}
    assert _post({"session_id": _SID, "score": 0.5, "category_scores": _cats}).status_code == 422


@pytest.mark.parametrize("key", [None, "wrong-key-1234567"])
def test_callback_requires_the_api_key(pending, key) -> None:
    assert _post({"session_id": _SID, "score": 1.0}, key=key).status_code == 401
    assert not pending.event.is_set()


def test_unknown_session_is_404() -> None:
    assert _post({"session_id": "zzzzzz0123456789", "score": 1.0}).status_code == 404
