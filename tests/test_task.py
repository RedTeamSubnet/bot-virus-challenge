import re

from src.bv_challenge.challenge.api.endpoints.challenge import task as task_mod


def _task(**overrides) -> dict:
    base = {
        "seed": "a1b2c3d4e5f6a7b8",
        "code": "K7Q2XM",
        "cardCount": 24,
        "codeCardIndex": 17,
        "sliderTarget": 63,
        "sliderTolerance": 3,
    }
    base.update(overrides)
    return base


def _payload(code="K7Q2XM", slider=63, completed=True) -> dict:
    return {
        "schemaVersion": "3",
        "task": {
            "seed": "a1b2c3d4e5f6a7b8",
            "answer": {"code": code, "slider": slider},
            "steps": [],
            "completed": completed,
        },
    }


def test_generate_task_shape_and_ranges() -> None:
    for _ in range(200):
        task = task_mod.generate_task(18, 30, 3)
        assert set(task) == {
            "seed",
            "code",
            "cardCount",
            "codeCardIndex",
            "sliderTarget",
            "sliderTolerance",
        }
        assert re.fullmatch(r"[0-9a-f]{16}", task["seed"])
        assert re.fullmatch(r"[A-Z2-9]{6}", task["code"])
        assert 18 <= task["cardCount"] <= 30
        assert 0 <= task["codeCardIndex"] < task["cardCount"]
        assert 0 <= task["sliderTarget"] <= 100
        assert task["sliderTolerance"] == 3


def test_generate_task_handles_a_single_value_range() -> None:
    for lo, hi in ((6, 6),):
        task = task_mod.generate_task(lo, hi, 0)
        assert 0 <= task["codeCardIndex"] < task["cardCount"]


def test_generate_task_is_random() -> None:
    seeds = {task_mod.generate_task(18, 30, 3)["seed"] for _ in range(20)}
    assert len(seeds) == 20


def test_answer_accepts_exact_and_case_insensitive_code() -> None:
    assert task_mod.check_task_answer(_task(), _payload())
    assert task_mod.check_task_answer(_task(), _payload(code="k7q2xm"))


def test_answer_rejects_wrong_code() -> None:
    assert not task_mod.check_task_answer(_task(), _payload(code="K7Q2XN"))
    assert not task_mod.check_task_answer(_task(), _payload(code=None))


def test_answer_slider_tolerance_boundaries() -> None:
    assert task_mod.check_task_answer(_task(), _payload(slider=60))
    assert task_mod.check_task_answer(_task(), _payload(slider=66))
    assert not task_mod.check_task_answer(_task(), _payload(slider=59))
    assert not task_mod.check_task_answer(_task(), _payload(slider=67))
    assert not task_mod.check_task_answer(_task(), _payload(slider="63"))
    assert not task_mod.check_task_answer(_task(), _payload(slider=True))


def test_answer_requires_completed_true() -> None:
    assert not task_mod.check_task_answer(_task(), _payload(completed=False))


def test_answer_rejects_missing_sections_and_missing_expected() -> None:
    assert not task_mod.check_task_answer(_task(), {})
    assert not task_mod.check_task_answer(_task(), {"task": {"completed": True}})
    assert not task_mod.check_task_answer(None, _payload())


def test_config_rejects_inverted_card_range() -> None:
    import pytest
    from pydantic import ValidationError

    from src.bv_challenge.challenge.api.main import app  # noqa: F401  (sets up `api` imports)
    from api.core.configs._challenge import ChallengeConfig

    base = dict(n_run_per_ch=2, bot_timeout=10, vm_endpoint="http://x", api_key="test-key-123")
    ChallengeConfig(**base, task_card_count_min=10, task_card_count_max=10)
    with pytest.raises(ValidationError):
        ChallengeConfig(**base, task_card_count_min=20, task_card_count_max=10)
