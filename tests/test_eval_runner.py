"""Service-level tests for /_eval attribution + /score orchestration.

These exercise the framework-free core extracted into ``eval_runner`` together
with the real ``RunStore``, so the concurrency/attribution behavior is covered
without the global TaskManager, FastAPI, config, or the crypto/detector wheels.

Covered: duplicate callbacks, out-of-order callbacks, unattributable payloads,
stale/late callbacks from a previous run, runner failure, timeout padding, and
score aggregation.
"""

import pytest

from src.bv_challenge.challenge.api.endpoints.challenge import eval_runner
from src.bv_challenge.challenge.api.endpoints.challenge.eval_runner import (
    EvalOutcome,
    process_eval,
    run_scoring,
)
from src.bv_challenge.challenge.api.endpoints.challenge.session_store import RunStore


# --------------------------------------------------------------------------- #
# Test crypto stub: a payload "encrypted" for a session is "<private_key>|<json>".
# Only the matching private key decrypts it; every other key raises -- mirroring
# real per-session RSA trial-decryption without the wheel.
# --------------------------------------------------------------------------- #
def _encrypt(private_key: str, body: str = '{"ok": true}') -> str:
    return f"{private_key}|{body}"


def _decrypt(ciphertext: str, private_key: str) -> str:
    tag, sep, body = ciphertext.partition("|")
    if not sep or tag != private_key:
        raise ValueError("wrong key")
    return body


def _const_score(value: float):
    return lambda _data: value


def _make_store(*sessions) -> RunStore:
    # sessions: (session_id, private_key) pairs.
    return RunStore.create("run", list(sessions))


# ----------------------------- process_eval -------------------------------- #
def test_attributes_and_records_by_trial_decryption() -> None:
    store = _make_store(("a", "ka"), ("b", "kb"))

    outcome = process_eval(
        store, _encrypt("kb"), decrypt_fn=_decrypt, score_fn=_const_score(0.7)
    )

    assert outcome == EvalOutcome(status="recorded", session_id="b", score=0.7)
    assert store.get_score("b") == 0.7
    assert store.get_score("a") is None  # untouched


def test_out_of_order_callbacks_each_attributed_correctly() -> None:
    store = _make_store(("a", "ka"), ("b", "kb"), ("c", "kc"))

    # Arrive c, a, b -- order independent.
    for sid, score in (("kc", 0.3), ("ka", 0.9), ("kb", 0.6)):
        process_eval(
            store, _encrypt(sid), decrypt_fn=_decrypt, score_fn=_const_score(score)
        )

    assert store.get_score("a") == 0.9
    assert store.get_score("b") == 0.6
    assert store.get_score("c") == 0.3
    assert store.completed_count() == 3


def test_duplicate_callback_is_ignored_and_not_recounted() -> None:
    store = _make_store(("a", "ka"))
    first = process_eval(
        store, _encrypt("ka"), decrypt_fn=_decrypt, score_fn=_const_score(0.8)
    )
    assert first.status == "recorded"

    # Same session reports again with a different score -> ignored, original kept.
    second = process_eval(
        store, _encrypt("ka"), decrypt_fn=_decrypt, score_fn=_const_score(0.1)
    )
    assert second == EvalOutcome(status="duplicate", session_id="a")
    assert store.get_score("a") == 0.8
    assert store.completed_count() == 1


def test_duplicate_does_not_invoke_scorer() -> None:
    store = _make_store(("a", "ka"))
    process_eval(store, _encrypt("ka"), decrypt_fn=_decrypt, score_fn=_const_score(0.5))

    def _boom(_data):
        raise AssertionError("scorer must not run for a duplicate callback")

    outcome = process_eval(store, _encrypt("ka"), decrypt_fn=_decrypt, score_fn=_boom)
    assert outcome.status == "duplicate"


def test_unattributable_payload_is_ignored_and_consumes_nothing() -> None:
    store = _make_store(("a", "ka"), ("b", "kb"))

    # Encrypted for a key not in this run (garbage/tampered/wrong key).
    outcome = process_eval(
        store, _encrypt("kZ"), decrypt_fn=_decrypt, score_fn=_const_score(0.9)
    )

    assert outcome == EvalOutcome(status="unattributable")
    # No session was consumed or charged.
    assert store.completed_count() == 0
    assert store.get_score("a") is None
    assert store.get_score("b") is None


def test_malformed_ciphertext_is_unattributable() -> None:
    store = _make_store(("a", "ka"))
    outcome = process_eval(
        store, "not-a-valid-ciphertext", decrypt_fn=_decrypt, score_fn=_const_score(1.0)
    )
    assert outcome.status == "unattributable"
    assert store.completed_count() == 0


def test_stale_callback_from_previous_run_is_ignored() -> None:
    # Run 1 completes.
    run1 = _make_store(("a", "k1a"), ("b", "k1b"))
    process_eval(run1, _encrypt("k1a"), decrypt_fn=_decrypt, score_fn=_const_score(1.0))
    run1.finalize(timeout_score=0.0)

    # Run 2 starts with fresh keys; a late callback from run 1 arrives.
    run2 = _make_store(("a", "k2a"), ("b", "k2b"))
    outcome = process_eval(
        run2, _encrypt("k1a"), decrypt_fn=_decrypt, score_fn=_const_score(1.0)
    )

    # run1's key does not exist in run2 -> unattributable -> ignored.
    assert outcome.status == "unattributable"
    assert run2.completed_count() == 0


def test_record_race_collapses_to_duplicate() -> None:
    # A store whose session looks open (is_completed False) but record() loses the
    # race (returns False) -- the concurrent-callback branch must report duplicate.
    class _RacyStore:
        private_keys = {"a": "ka"}

        def is_completed(self, _sid):
            return False

        def record(self, _sid, _score):
            return False

        def finalize(self, timeout_score):
            return 0.0

    outcome = process_eval(
        _RacyStore(), _encrypt("ka"), decrypt_fn=_decrypt, score_fn=_const_score(0.5)
    )
    assert outcome == EvalOutcome(status="duplicate", session_id="a")


# ----------------------------- run_scoring --------------------------------- #
def test_run_scoring_returns_runner_fail_score_and_skips_wait() -> None:
    store = _make_store(("a", "ka"), ("b", "kb"))
    waited = []

    def _start():
        raise RuntimeError("runner down")

    def _wait():
        waited.append(True)

    score = run_scoring(
        store=store,
        start_runner=_start,
        wait_for_completion=_wait,
        timeout_score=0.0,
        runner_fail_score=-1.0,
    )

    assert score == -1.0
    assert waited == []  # never waited
    assert store.completed_count() == 0  # store untouched


def test_run_scoring_invokes_on_runner_error() -> None:
    store = _make_store(("a", "ka"))
    seen = {}

    run_scoring(
        store=store,
        start_runner=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        wait_for_completion=lambda: None,
        timeout_score=0.0,
        runner_fail_score=0.0,
        on_runner_error=lambda err: seen.setdefault("err", str(err)),
    )

    assert seen["err"] == "boom"


def test_run_scoring_aggregates_over_expected_with_timeout_padding() -> None:
    store = _make_store(("a", "ka"), ("b", "kb"), ("c", "kc"))

    def _start():
        return None

    def _wait():
        # Two of three sessions report during the wait; "c" never does.
        process_eval(
            store, _encrypt("ka"), decrypt_fn=_decrypt, score_fn=_const_score(1.0)
        )
        process_eval(
            store, _encrypt("kb"), decrypt_fn=_decrypt, score_fn=_const_score(0.4)
        )

    score = run_scoring(
        store=store,
        start_runner=_start,
        wait_for_completion=_wait,
        timeout_score=0.0,
        runner_fail_score=0.0,
    )

    # (1.0 + 0.4 + timeout 0.0) / expected 3
    assert score == pytest.approx((1.0 + 0.4 + 0.0) / 3)
    assert store.sessions["c"].timed_out is True


def test_run_scoring_full_completion_averages_all() -> None:
    store = _make_store(("a", "ka"), ("b", "kb"))

    def _wait():
        process_eval(
            store, _encrypt("ka"), decrypt_fn=_decrypt, score_fn=_const_score(0.6)
        )
        process_eval(
            store, _encrypt("kb"), decrypt_fn=_decrypt, score_fn=_const_score(0.8)
        )

    score = run_scoring(
        store=store,
        start_runner=lambda: None,
        wait_for_completion=_wait,
        timeout_score=0.0,
        runner_fail_score=0.0,
    )
    assert score == pytest.approx((0.6 + 0.8) / 2)
