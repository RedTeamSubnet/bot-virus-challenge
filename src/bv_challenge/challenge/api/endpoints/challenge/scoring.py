# -*- coding: utf-8 -*-

"""Public scoring boundary (Layer 2).

The detection logic itself lives in the compiled, private ``rt_bv_score``
wheel (shipped as a binary ``.so`` like ``vault_unlock``) so the algorithm is
not readable in this public repo. This module only provides a safe wrapper:
it calls the detector, validates its result, and falls back to a neutral
score on any failure.
"""

import logging
import math
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

METRICS_PROCESSOR_ERROR_SCORE = 0.5

try:
    from rt_bv_score import MetricsProcessor as _default_metrics_processor
    from rt_bv_score import gate as _default_gate
except ImportError:  # private detector wheel not installed (local dev / CI)
    _default_metrics_processor = None
    _default_gate = None
    logger.warning(
        "rt_bv_score is not installed; scoring falls back to the error score "
        "and the Layer 1 gate is skipped unless injected."
    )


# --- Layer 1 (public): structural shape validation --------------------------
# This is deliberately *basic and non-secret*: it only checks that the decrypted
# payload has the expected containers and that the (additive) advanced-signal
# fields, when present, are the right type. It contains NO thresholds, weights,
# or anti-bot heuristics — all of that lives in the private detector. Keeping a
# cheap public shape gate here lets us reject obviously malformed / tampered
# payloads before they ever reach the private wheel.

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
# compatible — older payloads without them still pass this public gate).
_OPTIONAL_LIST_FIELDS = ("eventSequence", "targets")
_OPTIONAL_DICT_FIELDS = ("pageTimings", "trustedEventStats", "taskProgress")


def validate_shape(data: Any) -> tuple[bool, str | None]:
    """Public Layer 1 shape check. Returns (ok, reason).

    Structural only: confirms the payload is a dict, the legacy raw series are
    lists, and the advanced-signal fields (browserInfo / pageTimings /
    eventSequence / targets / trustedEventStats / taskProgress) are correctly
    typed when present. No behavioral judgement is made here.
    """
    if not isinstance(data, dict):
        return False, "payload is not an object"

    for _field in _REQUIRED_LIST_FIELDS:
        if _field not in data:
            return False, f"missing required field: {_field}"
        if not isinstance(data[_field], list):
            return False, f"field is not a list: {_field}"

    for _field in _OPTIONAL_LIST_FIELDS:
        if _field in data and not isinstance(data[_field], list):
            return False, f"field is not a list: {_field}"

    for _field in _OPTIONAL_DICT_FIELDS:
        if _field in data and not isinstance(data[_field], dict):
            return False, f"field is not an object: {_field}"

    # browserInfo is an object when present, but may legitimately be null when the
    # environment snapshot was unavailable in the browser.
    if "browserInfo" in data and data["browserInfo"] is not None:
        if not isinstance(data["browserInfo"], dict):
            return False, "field is not an object: browserInfo"

    return True, None


def passes_gate(
    data: dict,
    gate: Callable[[dict], dict] | None = None,
) -> tuple[bool, str | None]:
    """Run the Layer 1 gate. Returns (passed, reason).

    Fails *closed* (rejects) on a malformed gate result or a gate error, since
    the gate guards against adversarial payloads. When no gate is available
    (dev/CI without the wheel) it passes through so the scorer's own fallback
    governs the outcome.
    """
    gate_fn = gate or _default_gate
    if gate_fn is None:
        return True, "gate unavailable (dev passthrough)"

    try:
        result = gate_fn(data)
    except Exception as err:
        logger.exception("Layer 1 gate failed: %s", err)
        return False, "gate error"

    if not isinstance(result, dict) or "passed" not in result:
        logger.warning("Gate returned malformed result: %r", result)
        return False, "gate malformed result"

    return bool(result.get("passed")), result.get("reason")


def score_with_metrics_processor(
    data: dict,
    metrics_processor: Callable[[dict], dict] | None = None,
    error_score: float = METRICS_PROCESSOR_ERROR_SCORE,
) -> float:
    """Run the detector on a decrypted payload and return a clamped 0..1 score."""
    processor = metrics_processor or _default_metrics_processor
    if processor is None:
        logger.error("No MetricsProcessor available; returning error score.")
        return error_score

    try:
        result = processor(data)
    except Exception as err:
        logger.exception("MetricsProcessor failed: %s", err)
        return error_score

    if not isinstance(result, dict):
        logger.warning("MetricsProcessor returned non-dict result: %r", result)
        return error_score

    score = _coerce_score(result.get("score"))
    if score is None:
        logger.warning("MetricsProcessor returned invalid score: %r", result.get("score"))
        return error_score

    score_message = result.get("score_message")
    if score_message:
        logger.info("MetricsProcessor score_message: %s", score_message)

    return min(max(score, 0.0), 1.0)


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


__all__ = ["validate_shape", "passes_gate", "score_with_metrics_processor"]
