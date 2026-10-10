import math

from src.bv_challenge.challenge.api.endpoints.challenge import scoring
from src.bv_challenge.challenge.api.endpoints.challenge.scoring import (
    score_with_metrics_processor,
)


def test_score_with_metrics_processor_returns_valid_score() -> None:
    result = score_with_metrics_processor(
        {},
        metrics_processor=lambda data: {"score": "0.72", "score_message": "internal"},
    )

    assert result.score == 0.72


def test_score_with_metrics_processor_falls_back_on_bad_values() -> None:
    bad_processors = [
        lambda data: {},
        lambda data: {"score": "not-a-number"},
        lambda data: {"score": math.nan},
        lambda data: {"score": math.inf},
    ]

    for processor in bad_processors:
        assert score_with_metrics_processor({}, metrics_processor=processor).score == 0.5


def test_score_with_metrics_processor_falls_back_on_exception() -> None:
    def raises(data: dict) -> dict:
        raise RuntimeError("boom")

    assert score_with_metrics_processor({}, metrics_processor=raises).score == 0.5


def test_score_with_metrics_processor_clamps_out_of_range_scores() -> None:
    assert (
        score_with_metrics_processor({}, metrics_processor=lambda data: {"score": 1.4}).score
        == 1.0
    )
    assert (
        score_with_metrics_processor({}, metrics_processor=lambda data: {"score": -0.2}).score
        == 0.0
    )


def test_returns_error_score_when_no_processor_available(monkeypatch) -> None:
    # Simulates the rt_bv_score wheel being absent: default processor is None,
    # so the wrapper must fall back to the configured error score.
    monkeypatch.setattr(scoring, "_default_metrics_processor", None)
    assert scoring.score_with_metrics_processor({}, error_score=0.5).score == 0.5



def test_result_reports_hard_fail_with_reason() -> None:
    result = scoring.score_with_metrics_processor(
        {},
        metrics_processor=lambda data: {
            "score": 0.7,
            "score_message": "session=0.7",
            "hard_fail": True,
            "hard_fail_reason": "automation",
        },
    )

    assert result.hard_fail is True
    assert result.hard_fail_reason == "automation"
    assert result.score == 0.0  # a hard fail always scores zero


def test_result_without_hard_fail_keys_is_not_a_hard_fail() -> None:
    # Older scorer wheels do not return the keys at all.
    result = scoring.score_with_metrics_processor(
        {}, metrics_processor=lambda data: {"score": 0.6, "score_message": "session=0.6"}
    )

    assert result.hard_fail is False
    assert result.hard_fail_reason is None
    assert result.score == 0.6
    assert result.category_scores == {"session": 0.6}


def test_result_hard_fail_false_keeps_score_and_unknown_reason_is_dropped() -> None:
    result = scoring.score_with_metrics_processor(
        {},
        metrics_processor=lambda data: {
            "score": 0.9,
            "hard_fail": False,
            "hard_fail_reason": "automation",
        },
    )
    assert (result.score, result.hard_fail, result.hard_fail_reason) == (0.9, False, None)

    result = scoring.score_with_metrics_processor(
        {},
        metrics_processor=lambda data: {
            "score": 0.9,
            "hard_fail": True,
            "hard_fail_reason": "some-private-detail",
        },
    )
    assert result.hard_fail is True
    assert result.hard_fail_reason is None
