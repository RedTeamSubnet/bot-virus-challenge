from api.endpoints.challenge import service


def test_feedback_band_has_two_levels():
    assert service._feedback_band(1.0) == "ok"
    assert service._feedback_band(service.FEEDBACK_OK_MIN) == "ok"
    assert service._feedback_band(service.FEEDBACK_OK_MIN - 0.01) == "needs_work"
    assert service._feedback_band(0.0) == "needs_work"


def test_miner_feedback_hides_limit_and_unknown_categories():
    feedback = service._miner_feedback(
        {"session": 1.0, "client": 0.83, "activity": 0.9, "limit": 0.45, "other": 0.1}
    )
    assert feedback == {"session": "ok", "client": "needs_work", "activity": "ok"}
