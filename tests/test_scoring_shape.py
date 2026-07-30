"""Tests for the public Layer 1 shape validator.

The validator is intentionally structural only (no thresholds / weights / secret
heuristics — those live in the private detector). It must accept well-formed
payloads and reject malformed / tampered ones before they reach the wheel.
"""

from src.bv_challenge.challenge.api.endpoints.challenge import scoring


def _valid_payload() -> dict:
    return {
        "movements": [{"x": 1, "y": 2, "t": 3}],
        "clicks": [],
        "mouseDowns": [],
        "mouseUps": [],
        "keydowns": [{"t": 1}],
        "keyups": [{"t": 2}],
        "scroll": [],
        "browserInfo": {"userAgent": "x", "webdriver": False},
        "pageTimings": {"pageLoadMs": 800},
        "eventSequence": [{"type": "click", "t": 1, "pt": 0.5, "trusted": True}],
        "targets": [],
        "trustedEventStats": {"click": {"trusted": 1, "untrusted": 0}},
        "taskProgress": {"usernameFocused": True},
    }


def test_accepts_well_formed_payload() -> None:
    ok, reason = scoring.validate_shape(_valid_payload())
    assert ok is True
    assert reason is None


def test_accepts_legacy_counts_only_payload() -> None:
    # Older payloads without the advanced fields still pass (forward/backward compat).
    legacy = {k: [] for k in ("movements", "clicks", "mouseDowns", "mouseUps", "keydowns", "keyups", "scroll")}
    ok, _ = scoring.validate_shape(legacy)
    assert ok is True


def test_accepts_null_browser_info() -> None:
    payload = _valid_payload()
    payload["browserInfo"] = None  # environment snapshot may be unavailable
    ok, _ = scoring.validate_shape(payload)
    assert ok is True


def test_rejects_non_dict() -> None:
    for _bad in ([], "x", 5, None):
        ok, reason = scoring.validate_shape(_bad)
        assert ok is False
        assert reason


def test_rejects_missing_required_field() -> None:
    payload = _valid_payload()
    del payload["movements"]
    ok, reason = scoring.validate_shape(payload)
    assert ok is False
    assert "movements" in reason


def test_rejects_required_field_wrong_type() -> None:
    payload = _valid_payload()
    payload["clicks"] = {"not": "a list"}
    ok, reason = scoring.validate_shape(payload)
    assert ok is False
    assert "clicks" in reason


def test_rejects_optional_list_field_wrong_type() -> None:
    payload = _valid_payload()
    payload["eventSequence"] = "tampered"
    ok, reason = scoring.validate_shape(payload)
    assert ok is False
    assert "eventSequence" in reason


def test_rejects_optional_dict_field_wrong_type() -> None:
    payload = _valid_payload()
    payload["taskProgress"] = ["nope"]
    ok, reason = scoring.validate_shape(payload)
    assert ok is False
    assert "taskProgress" in reason


def test_rejects_non_dict_browser_info() -> None:
    payload = _valid_payload()
    payload["browserInfo"] = "tampered"
    ok, reason = scoring.validate_shape(payload)
    assert ok is False
    assert "browserInfo" in reason
