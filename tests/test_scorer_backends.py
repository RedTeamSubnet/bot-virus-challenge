import pytest
import requests

from api.endpoints.challenge import scoring
from api.endpoints.challenge.lite_scorer import metrics_processor as lite


def _payload(**overrides):
    data = {
        "browserInfo": {"webdriver": False},
        "task": {"completed": True},
        "movements": [{"x": 1, "y": 2, "t": 3.0, "trusted": True}],
        "clicks": [],
        "keydowns": [{"t": 4.0, "trusted": True, "code": "KeyA"}],
        "eventSequence": [{"type": "click", "t": 5.0, "trusted": True}],
    }
    data.update(overrides)
    return data


def test_lite_scorer_follows_the_metrics_processor_contract() -> None:
    result = lite(_payload())
    assert set(result) == {"score", "score_message", "hard_fail", "hard_fail_reason"}
    assert result["hard_fail"] is False
    assert 0.0 <= result["score"] <= 1.0
    assert "lite=" in result["score_message"]


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"browserInfo": {"webdriver": True}}, "automation"),
        ({"eventSequence": [{"type": "click", "t": 5.0, "trusted": False}]}, "untrusted_input"),
    ],
)
def test_lite_scorer_hard_fails_public_tells(overrides, reason) -> None:
    result = lite(_payload(**overrides))
    assert (result["score"], result["hard_fail"], result["hard_fail_reason"]) == (0.0, True, reason)
    assert reason in scoring.HARD_FAIL_REASONS


def test_lite_scorer_survives_malformed_sections() -> None:
    result = lite({"browserInfo": "x", "task": None, "movements": "y", "keydowns": [1, None]})
    assert result["hard_fail"] is False
    assert scoring.score_with_metrics_processor({}, metrics_processor=lite).score_source == "scorer"


def test_dev_scorer_failure_is_an_infrastructure_error_not_a_score() -> None:
    # DH: no 0.5 fallback. The per-session value is never used: the service
    # aborts any run that contains this outcome (test_score_flow).
    def broken(_data):
        raise requests.ConnectionError("down")

    result = scoring.score_with_metrics_processor({}, metrics_processor=broken, error_score=0.5)
    assert (result.outcome, result.score_source) == ("infrastructure_error", "fallback")
    assert result.outcome in scoring.OUTCOMES_ABORTING_THE_RUN


def test_backend_resolution() -> None:
    # Remote scoring is the signed client in the service, never a processor.
    with pytest.raises(ValueError):
        scoring.resolve_metrics_processor("auto", "https://scorer:9443")
    with pytest.raises(ValueError):
        scoring.resolve_metrics_processor("remote", "https://scorer:9443")
    assert scoring.resolve_metrics_processor("lite") is lite
    # None = the local wheel, looked up per call (so it can be swapped in tests).
    assert scoring.resolve_metrics_processor("local") is None
    assert scoring.resolve_metrics_processor("auto") is None
    assert not hasattr(scoring, "RemoteMetricsProcessor")  # no unsigned remote path
