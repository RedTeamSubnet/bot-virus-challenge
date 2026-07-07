import math

from src.bv_challenge.challenge.api.endpoints.challenge import scoring
from src.bv_challenge.challenge.api.endpoints.challenge.scoring import (
    score_with_metrics_processor,
)


def test_score_with_metrics_processor_returns_valid_score() -> None:
    score = score_with_metrics_processor(
        {},
        metrics_processor=lambda data: {"score": "0.72", "score_message": "internal"},
    )

    assert score == 0.72


def test_score_with_metrics_processor_falls_back_on_bad_values() -> None:
    bad_processors = [
        lambda data: {},
        lambda data: {"score": "not-a-number"},
        lambda data: {"score": math.nan},
        lambda data: {"score": math.inf},
    ]

    for processor in bad_processors:
        assert score_with_metrics_processor({}, metrics_processor=processor) == 0.5


def test_score_with_metrics_processor_falls_back_on_exception() -> None:
    def raises(data: dict) -> dict:
        raise RuntimeError("boom")

    assert score_with_metrics_processor({}, metrics_processor=raises) == 0.5


def test_score_with_metrics_processor_clamps_out_of_range_scores() -> None:
    assert (
        score_with_metrics_processor({}, metrics_processor=lambda data: {"score": 1.4})
        == 1.0
    )
    assert (
        score_with_metrics_processor({}, metrics_processor=lambda data: {"score": -0.2})
        == 0.0
    )


def test_returns_error_score_when_no_processor_available(monkeypatch) -> None:
    # Simulates the rt_bv_score wheel being absent: default processor is None,
    # so the wrapper must fall back to the configured error score.
    monkeypatch.setattr(scoring, "_default_metrics_processor", None)
    assert scoring.score_with_metrics_processor({}, error_score=0.5) == 0.5

