# -*- coding: utf-8 -*-

"""Public scoring boundary.

The detection logic itself lives in the compiled, private ``rt_bv_score``
wheel (shipped as a binary ``.so`` like ``vault_unlock``) so the algorithm is
not readable in this public repo. This module only provides a safe wrapper:
it calls the detector, validates its result, and falls back to a neutral score
on any failure.
"""

import logging
import math
from collections.abc import Callable
from typing import Any, NamedTuple

logger = logging.getLogger(__name__)

METRICS_PROCESSOR_ERROR_SCORE = 0.5

# The only payload schema the challenge API accepts. Older schemas are
# rejected here so a client cannot skip the v3 checks by downgrading.
SCHEMA_VERSION = "3"

# Coarse hard-fail reason codes (shared contract). Nothing finer is exposed.
HARD_FAIL_REASONS = frozenset(
    {"automation", "integrity", "realm_mismatch", "untrusted_input", "payload", "task"}
)


class ScoreResult(NamedTuple):
    """Outcome of scoring one session."""

    score: float
    category_scores: dict[str, float]
    hard_fail: bool = False
    hard_fail_reason: str | None = None
    # Operator diagnostics only; never returned to miners (see service.get_result).
    score_source: str = "scorer"  # "scorer" | "fallback" | "gate"
    scorer_error_type: str | None = None

    @property
    def fallback_used(self) -> bool:
        return self.score_source == "fallback"


def _fallback(error_score: float, error_type: str) -> ScoreResult:
    return ScoreResult(error_score, {}, score_source="fallback", scorer_error_type=error_type)

try:
    from rt_bv_score import MetricsProcessor as _default_metrics_processor
except ImportError:  # private detector wheel not installed (local dev / CI)
    _default_metrics_processor = None
    logger.warning(
        "rt_bv_score is not installed; scoring falls back to the error score "
        "unless a metrics processor is injected."
    )


# --- Public structural shape validation -------------------------------------
# This is deliberately *basic and non-secret*: it only checks that the decrypted
# payload has the expected containers and that the (additive) advanced-signal
# fields, when present, are the right type. It contains NO thresholds, weights,
# or anti-bot heuristics — all of that lives in the private detector. Keeping a
# cheap public shape validation here lets us reject obviously malformed /
# tampered payloads before they ever reach the private wheel.

# Legacy raw series that every well-formed payload must carry as lists.
_REQUIRED_LIST_FIELDS = (
    "movements",
    "clicks",
    "mouseDowns",
    "mouseUps",
    "keydowns",
    "keyups",
    "scroll",
)
# Advanced raw signals: validated only for *shape* when present (forward/backward
# compatible — older payloads without them still pass this public validation).
_OPTIONAL_LIST_FIELDS = ("eventSequence", "targets")
_OPTIONAL_DICT_FIELDS = (
    "pageTimings",
    "trustedEventStats",
    "taskProgress",
    "cdpSignals",
    "collectorIntegrity",
    "submissionContext",
)
_REQUIRED_DICT_FIELDS = (
    "browserInfo",
    "runtimeIntegrity",
    "fingerprint",
    "apiAvailability",
    "sessionBinding",
    "collectorIntegrity",
    "submissionContext",
    "task",
    "crossRealm",
    "errorFingerprint",
    "stackTraps",
    "hardware",
    "capabilities",
)


def validate_shape(data: Any) -> tuple[bool, str | None]:
    """Public Layer 1 shape check. Returns (ok, reason).

    Structural only: confirms the payload is a schema ``SCHEMA_VERSION`` dict,
    the raw event series are lists, every required section is an object, and
    the optional fields are correctly typed when present. No behavioral
    judgement is made here.
    """
    if not isinstance(data, dict):
        return False, "payload is not an object"

    if str(data.get("schemaVersion")) != SCHEMA_VERSION:
        return False, "unsupported schemaVersion"

    for _field in _REQUIRED_LIST_FIELDS:
        if _field not in data:
            return False, f"missing required field: {_field}"
        if not isinstance(data[_field], list):
            return False, f"field is not a list: {_field}"

    for _field in _REQUIRED_DICT_FIELDS:
        if not isinstance(data.get(_field), dict):
            return False, f"field is not an object: {_field}"

    if "steps" in data["task"] and not isinstance(data["task"]["steps"], list):
        return False, "field is not a list: task.steps"
    if "hits" in data["stackTraps"] and not isinstance(data["stackTraps"]["hits"], list):
        return False, "field is not a list: stackTraps.hits"

    for _field in _OPTIONAL_LIST_FIELDS:
        if _field in data and not isinstance(data[_field], list):
            return False, f"field is not a list: {_field}"

    for _field in _OPTIONAL_DICT_FIELDS:
        if _field in data and not isinstance(data[_field], dict):
            return False, f"field is not an object: {_field}"

    return True, None


def score_with_metrics_processor(
    data: dict,
    metrics_processor: Callable[[dict], dict] | None = None,
    error_score: float = METRICS_PROCESSOR_ERROR_SCORE,
) -> ScoreResult:
    """Run the detector; return score, category scores and the hard-fail flag.

    A scorer that does not report ``hard_fail`` (older wheel) is not a hard fail.
    """
    processor = metrics_processor or _default_metrics_processor
    if processor is None:
        logger.error("No MetricsProcessor available; returning error score.")
        return _fallback(error_score, "unavailable")

    try:
        result = processor(data)
    except Exception as err:
        logger.exception("MetricsProcessor failed: %s", err)
        return _fallback(error_score, "exception")

    if not isinstance(result, dict):
        logger.warning("MetricsProcessor returned non-dict result: %r", result)
        return _fallback(error_score, "invalid_result")

    score = _coerce_score(result.get("score"))
    if score is None:
        logger.warning("MetricsProcessor returned invalid score: %r", result.get("score"))
        return _fallback(error_score, "invalid_score")

    score_message = result.get("score_message")
    category_scores = _parse_score_message(score_message)
    if score_message:
        logger.info("MetricsProcessor score_message: %s", score_message)

    if result.get("hard_fail") is True:
        reason = result.get("hard_fail_reason")
        reason = reason if isinstance(reason, str) and reason in HARD_FAIL_REASONS else None
        return ScoreResult(0.0, category_scores, True, reason)

    return ScoreResult(min(max(score, 0.0), 1.0), category_scores)


def _parse_score_message(score_message: Any) -> dict[str, float]:
    if not isinstance(score_message, str):
        return {}

    category_scores: dict[str, float] = {}
    for part in score_message.split(";"):
        label, separator, raw_value = part.strip().partition("=")
        if not separator or not label:
            continue
        value_text = raw_value.split(":", 1)[0].strip()
        value = _coerce_score(value_text)
        if value is not None:
            category_scores[label.strip()] = min(max(value, 0.0), 1.0)
    return category_scores


def _coerce_score(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None

    try:
        score = float(value)
    except (TypeError, ValueError):
        return None

    if math.isnan(score) or math.isinf(score):
        return None

    return score


__all__ = [
    "SCHEMA_VERSION",
    "HARD_FAIL_REASONS",
    "ScoreResult",
    "validate_shape",
    "score_with_metrics_processor",
]
