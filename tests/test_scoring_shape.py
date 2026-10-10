"""Tests for the public shape validator (structural only, no detection logic)."""

from src.bv_challenge.challenge.api.endpoints.challenge import scoring

_LISTS = ("movements", "clicks", "mouseDowns", "mouseUps", "keydowns", "keyups", "scroll")
_V2_OBJECTS = (
    "browserInfo",
    "runtimeIntegrity",
    "fingerprint",
    "apiAvailability",
    "sessionBinding",
    "collectorIntegrity",
    "submissionContext",
)
_V3_OBJECTS = ("task", "crossRealm", "errorFingerprint", "stackTraps", "hardware", "capabilities")


def _v2_payload() -> dict:
    payload: dict = {"schemaVersion": "2"}
    payload.update({name: [] for name in _LISTS})
    payload.update({name: {} for name in _V2_OBJECTS})
    payload["movements"] = [{"x": 1, "y": 2, "t": 3}]
    payload["eventSequence"] = [{"type": "click", "t": 1, "trusted": True}]
    payload["targets"] = []
    payload["pageTimings"] = {"pageLoadMs": 800}
    payload["trustedEventStats"] = {"click": {"trusted": 1, "untrusted": 0}}
    payload["taskProgress"] = {}
    return payload


def _v3_payload() -> dict:
    payload = _v2_payload()
    payload["schemaVersion"] = "3"
    payload.update({name: {} for name in _V3_OBJECTS})
    payload["stackTraps"] = {"hits": []}
    payload["task"] = {"steps": [], "completed": True}
    return payload


def test_accepts_well_formed_v3() -> None:
    assert scoring.validate_shape(_v3_payload()) == (True, None)


def test_rejects_schema_2_downgrade() -> None:
    # A client must not skip the v3 sections by claiming the old schema.
    assert scoring.validate_shape(_v2_payload()) == (False, "unsupported schemaVersion")


def test_requires_browser_info_object() -> None:
    for payload in (_v3_payload(),):
        payload["browserInfo"] = None
        # browserInfo must still be an object per the required v2 sections.
        ok, reason = scoring.validate_shape(payload)
        assert ok is False
        assert "browserInfo" in reason


def test_rejects_unsupported_schema_versions() -> None:
    for version in ("1", "4", None, 3.5):
        payload = _v3_payload()
        payload["schemaVersion"] = version
        ok, reason = scoring.validate_shape(payload)
        assert ok is False
        assert reason == "unsupported schemaVersion"


def test_accepts_numeric_schema_version() -> None:
    payload = _v3_payload()
    payload["schemaVersion"] = 3
    assert scoring.validate_shape(payload)[0] is True


def test_v3_requires_new_sections() -> None:
    for name in _V3_OBJECTS:
        payload = _v3_payload()
        del payload[name]
        ok, reason = scoring.validate_shape(payload)
        assert ok is False, name
        assert name in reason


def test_v3_sections_must_be_objects() -> None:
    for name in _V3_OBJECTS:
        payload = _v3_payload()
        payload[name] = []
        ok, reason = scoring.validate_shape(payload)
        assert ok is False, name
        assert name in reason


def test_v3_nested_lists_are_checked() -> None:
    payload = _v3_payload()
    payload["stackTraps"] = {"hits": "x"}
    assert scoring.validate_shape(payload)[0] is False
    payload = _v3_payload()
    payload["task"] = {"steps": {}}
    assert scoring.validate_shape(payload)[0] is False


def test_rejects_non_dict() -> None:
    for bad in ([], "x", 5, None):
        ok, reason = scoring.validate_shape(bad)
        assert ok is False
        assert reason


def test_rejects_missing_or_non_list_series() -> None:
    for name in _LISTS:
        payload = _v3_payload()
        del payload[name]
        ok, reason = scoring.validate_shape(payload)
        assert ok is False
        assert name in reason

        payload = _v3_payload()
        payload[name] = {"not": "a list"}
        ok, reason = scoring.validate_shape(payload)
        assert ok is False
        assert name in reason


def test_rejects_optional_fields_with_wrong_type() -> None:
    payload = _v3_payload()
    payload["eventSequence"] = "tampered"
    ok, reason = scoring.validate_shape(payload)
    assert ok is False and "eventSequence" in reason

    payload = _v3_payload()
    payload["taskProgress"] = ["nope"]
    ok, reason = scoring.validate_shape(payload)
    assert ok is False and "taskProgress" in reason
