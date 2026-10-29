# -*- coding: utf-8 -*-

"""Public development scorer (``scorer_backend: lite``).

A stand-in for the private detector so the challenge runs end to end without
it: build a bot, run the stack, see the pipeline work. It is NOT the real
detector and its score does not predict the real one. It only checks facts the
public SDK already reports (``browserInfo.webdriver``, ``isTrusted`` on input,
task completion and whether input happened at all) and contains no private
thresholds, weights or heuristics.

Contract: same as the private ``MetricsProcessor``.
"""

from typing import Any

# Event series whose items carry the SDK's ``trusted`` (``event.isTrusted``) flag.
_INPUT_SERIES = ("movements", "clicks", "mouseDowns", "mouseUps", "keydowns", "keyups", "eventSequence")


def _items(data: dict, key: str) -> list[dict]:
    value = data.get(key)
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def metrics_processor(data: dict[str, Any]) -> dict[str, Any]:
    browser = data.get("browserInfo") if isinstance(data.get("browserInfo"), dict) else {}
    task = data.get("task") if isinstance(data.get("task"), dict) else {}

    hard_fail_reason = None
    if browser.get("webdriver") is True:
        hard_fail_reason = "automation"
    elif any(item.get("trusted") is False for key in _INPUT_SERIES for item in _items(data, key)):
        hard_fail_reason = "untrusted_input"

    has_pointer = bool(_items(data, "movements")) or bool(_items(data, "clicks"))
    has_keys = bool(_items(data, "keydowns"))
    activity = (0.5 if has_pointer else 0.0) + (0.5 if has_keys else 0.0)
    session = 1.0 if task.get("completed") is True else 0.0
    client = 1.0
    score = 0.0 if hard_fail_reason else round((session + client + activity) / 3, 3)

    message = f"session={session:.3f}; client={client:.3f}; activity={activity:.3f}; lite=1.000"
    if hard_fail_reason:
        message += f"; hard_fail={hard_fail_reason}"
    return {
        "score": score,
        "score_message": message,
        "hard_fail": hard_fail_reason is not None,
        "hard_fail_reason": hard_fail_reason,
    }


__all__ = ["metrics_processor"]
