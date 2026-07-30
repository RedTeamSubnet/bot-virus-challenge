from src.bv_challenge.challenge.api.endpoints.challenge.session_store import RunStore


def test_record_once_and_dedup() -> None:
    store = RunStore.create("run1", [("a", "ka"), ("b", "kb")])
    assert store.record("a", 0.8) is True
    assert store.is_completed("a") is True
    assert store.get_score("a") == 0.8

    # Duplicate: not recorded again, original score preserved.
    assert store.record("a", 0.1) is False
    assert store.get_score("a") == 0.8
    assert store.completed_count() == 1


def test_record_unknown_session_is_rejected() -> None:
    store = RunStore.create("run1", [("a", "ka")])
    assert store.record("does-not-exist", 0.5) is False


def test_finalize_pads_missing_with_timeout_score() -> None:
    store = RunStore.create("run1", [("a", "ka"), ("b", "kb"), ("c", "kc")])
    store.record("a", 1.0)
    store.record("b", 0.5)

    # c never reported -> counts as timeout (0.0), averaged over expected 3.
    final = store.finalize(timeout_score=0.0)
    assert final == (1.0 + 0.5 + 0.0) / 3
    assert store.sessions["c"].timed_out is True


def test_finalize_averages_over_expected_not_completed() -> None:
    store = RunStore.create("run1", [("a", "ka"), ("b", "kb")])
    store.record("a", 1.0)
    # 1 of 2 completed -> average over the expected 2, not the 1 completed.
    assert store.finalize(timeout_score=0.0) == 0.5


def test_expected_sessions_tracks_creation() -> None:
    store = RunStore.create("run1", [("a", "ka"), ("b", "kb")])
    assert store.expected_sessions == 2
