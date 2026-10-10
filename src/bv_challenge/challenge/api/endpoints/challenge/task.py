# -*- coding: utf-8 -*-

"""Per-session interactive task: generation and the coarse answer check.

The page shows a scrolling card list (one card carries a code), a text input
and a slider. The server keeps the expected answer bound to the session nonce
and only compares it with what the page reports on submit. Behavioural
analysis is NOT done here; it lives in the private scorer.
"""

import secrets
from typing import Any, Dict, Optional

CODE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ23456789"
CODE_LENGTH = 6
TASK_FAIL_REASON = "task"


def generate_task(
    card_count_min: int,
    card_count_max: int,
    slider_tolerance: int,
) -> Dict[str, Any]:
    """Return a fresh task dict (the exact shape the page receives).

    The config guarantees ``card_count_min <= card_count_max``.
    """
    card_count = card_count_min + secrets.randbelow(card_count_max - card_count_min + 1)
    # Keep the code card in the lower half so it sits below the fold.
    first = min(max(card_count // 2, 5), card_count - 1)
    code_card_index = first + secrets.randbelow(card_count - first)
    return {
        "seed": secrets.token_hex(8),
        "code": "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH)),
        "cardCount": card_count,
        "codeCardIndex": code_card_index,
        "sliderTarget": secrets.randbelow(101),
        "sliderTolerance": int(slider_tolerance),
    }


def _as_number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def check_task_answer(expected: Optional[Dict[str, Any]], payload: Dict[str, Any]) -> bool:
    """True when the reported answer matches the stored one."""
    if not isinstance(expected, dict):
        return False
    task = payload.get("task")
    if not isinstance(task, dict) or task.get("completed") is not True:
        return False
    answer = task.get("answer")
    if not isinstance(answer, dict):
        return False

    code = answer.get("code")
    if not isinstance(code, str):
        return False
    if code.upper() != str(expected.get("code", "")).upper():
        return False

    slider = _as_number(answer.get("slider"))
    target = _as_number(expected.get("sliderTarget"))
    tolerance = _as_number(expected.get("sliderTolerance"))
    if slider is None or target is None or tolerance is None:
        return False
    return abs(slider - target) <= tolerance


__all__ = ["generate_task", "check_task_answer", "TASK_FAIL_REASON"]
