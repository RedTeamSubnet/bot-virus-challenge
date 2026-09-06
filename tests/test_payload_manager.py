import pytest

from src.bv_challenge.challenge.api.endpoints.challenge import (
    payload_manager as payload_manager_module,
)
from src.bv_challenge.challenge.api.endpoints.challenge.payload_manager import (
    EvalOutcome,
    PayloadManager,
)
from src.bv_challenge.challenge.api.endpoints.challenge.schemas import KeyPairPM


# A test payload is "<private_key>|<json body>": only the matching private key
# decrypts it, and every other key raises. That mirrors real per-session RSA
# trial decryption without pulling in the vault_unlock wheel.
def _encrypt(private_key: str, body: str = '{"ok": true}') -> str:
    return f"{private_key}|{body}"


def _decrypt(ciphertext: str, private_key: str) -> str:
    tag, separator, body = ciphertext.partition("|")
    if not separator or tag != private_key:
        raise ValueError("wrong key")
    return body


def _const_score(value: float, categories: dict | None = None):
    return lambda _payload: (value, categories or {})


def _private_key(session_id: str) -> str:
    # KeyPairPM requires a private key of at least 32 characters.
    return f"{session_id}-private-key".ljust(32, "0")


@pytest.fixture
def make_manager(monkeypatch):
    """Build a PayloadManager over known sessions, without generating real RSA
    key pairs. PayloadManager.reset() is the only thing that calls
    gen_key_pairs, so replacing it is enough to get a deterministic run."""

    def _make(*session_ids: str) -> PayloadManager:
        pairs = [
            KeyPairPM(
                private_key=_private_key(session_id),
                public_key=f"public-{session_id}",
                nonce=session_id,
            )
            for session_id in session_ids
        ]
        monkeypatch.setattr(
            payload_manager_module.challenge_utils,
            "gen_key_pairs",
            lambda: list(pairs),
        )
        return PayloadManager()

    return _make


def test_attributes_and_records_by_trial_decryption(make_manager) -> None:
    manager = make_manager("alpha", "bravo")

    outcome = manager.process_eval(
        _encrypt(_private_key("bravo")),
        decrypt_fn=_decrypt,
        score_fn=_const_score(0.7),
    )

    assert outcome == EvalOutcome(status="recorded", session_id="bravo", score=0.7)
    assert manager.sessions["bravo"].score == 0.7
    assert manager.sessions["alpha"].score is None


def test_out_of_order_callbacks_each_attributed_correctly(make_manager) -> None:
    manager = make_manager("alpha", "bravo", "charlie")

    for session_id, score in (("charlie", 0.3), ("alpha", 0.9), ("bravo", 0.6)):
        manager.process_eval(
            _encrypt(_private_key(session_id)),
            decrypt_fn=_decrypt,
            score_fn=_const_score(score),
        )

    assert manager.sessions["alpha"].score == 0.9
    assert manager.sessions["bravo"].score == 0.6
    assert manager.sessions["charlie"].score == 0.3
    assert manager.completed_count() == 3


def test_duplicate_callback_is_ignored_and_original_kept(make_manager) -> None:
    manager = make_manager("alpha")
    first = manager.process_eval(
        _encrypt(_private_key("alpha")),
        decrypt_fn=_decrypt,
        score_fn=_const_score(0.8),
    )
    assert first.status == "recorded"

    second = manager.process_eval(
        _encrypt(_private_key("alpha")),
        decrypt_fn=_decrypt,
        score_fn=_const_score(0.1),
    )

    assert second == EvalOutcome(status="duplicate", session_id="alpha")
    assert manager.sessions["alpha"].score == 0.8
    assert manager.completed_count() == 1


def test_duplicate_does_not_invoke_scorer(make_manager) -> None:
    manager = make_manager("alpha")
    manager.process_eval(
        _encrypt(_private_key("alpha")),
        decrypt_fn=_decrypt,
        score_fn=_const_score(0.5),
    )

    def _boom(_payload):
        raise AssertionError("scorer must not run for a duplicate callback")

    outcome = manager.process_eval(
        _encrypt(_private_key("alpha")), decrypt_fn=_decrypt, score_fn=_boom
    )

    assert outcome.status == "duplicate"


def test_unattributable_payload_consumes_nothing(make_manager) -> None:
    manager = make_manager("alpha", "bravo")

    outcome = manager.process_eval(
        _encrypt(_private_key("zulu")),
        decrypt_fn=_decrypt,
        score_fn=_const_score(0.9),
    )

    assert outcome == EvalOutcome(status="unattributable")
    assert manager.completed_count() == 0
    assert manager.sessions["alpha"].score is None
    assert manager.sessions["bravo"].score is None


def test_malformed_ciphertext_is_unattributable(make_manager) -> None:
    manager = make_manager("alpha")

    outcome = manager.process_eval(
        "not-a-valid-ciphertext", decrypt_fn=_decrypt, score_fn=_const_score(1.0)
    )

    assert outcome.status == "unattributable"
    assert manager.completed_count() == 0


def test_non_dict_payload_is_malformed(make_manager) -> None:
    manager = make_manager("alpha")

    outcome = manager.process_eval(
        _encrypt(_private_key("alpha"), body="[1, 2, 3]"),
        decrypt_fn=_decrypt,
        score_fn=_const_score(1.0),
    )

    assert outcome == EvalOutcome(status="malformed", session_id="alpha")
    assert manager.completed_count() == 0


def test_stale_callback_from_previous_run_is_ignored(make_manager) -> None:
    first_run = make_manager("alpha", "bravo")
    first_run.process_eval(
        _encrypt(_private_key("alpha")),
        decrypt_fn=_decrypt,
        score_fn=_const_score(1.0),
    )
    first_run.finalize(timeout_score=0.0)

    # A second run generates fresh sessions. A late callback carrying the first
    # run's key must not attribute to it.
    second_run = make_manager("charlie", "delta")
    outcome = second_run.process_eval(
        _encrypt(_private_key("alpha")),
        decrypt_fn=_decrypt,
        score_fn=_const_score(1.0),
    )

    assert outcome.status == "unattributable"
    assert second_run.completed_count() == 0


def test_trusted_request_replaces_any_client_supplied_value(make_manager) -> None:
    manager = make_manager("alpha")
    seen = {}

    def _capture(payload):
        seen.update(payload)
        return 1.0, {}

    manager.process_eval(
        _encrypt(
            _private_key("alpha"),
            body='{"_trustedRequest": {"userAgent": "forged"}}',
        ),
        decrypt_fn=_decrypt,
        score_fn=_capture,
        trusted_context={"userAgent": "real", "unknownHeader": "dropped"},
    )

    # Server-owned: the client value is replaced and unknown keys never land.
    assert seen["_trustedRequest"] == {"userAgent": "real"}


def test_finalize_pads_missing_sessions_with_timeout_score(make_manager) -> None:
    manager = make_manager("alpha", "bravo", "charlie")
    manager.process_eval(
        _encrypt(_private_key("alpha")),
        decrypt_fn=_decrypt,
        score_fn=_const_score(1.0),
    )
    manager.process_eval(
        _encrypt(_private_key("bravo")),
        decrypt_fn=_decrypt,
        score_fn=_const_score(0.5),
    )

    final = manager.finalize(timeout_score=0.0)

    assert final == pytest.approx((1.0 + 0.5 + 0.0) / 3)
    assert manager.sessions["charlie"].timed_out is True


def test_finalize_averages_over_expected_not_completed(make_manager) -> None:
    manager = make_manager("alpha", "bravo")
    manager.process_eval(
        _encrypt(_private_key("alpha")),
        decrypt_fn=_decrypt,
        score_fn=_const_score(1.0),
    )

    assert manager.finalize(timeout_score=0.0) == pytest.approx(0.5)


def test_finalize_category_scores_averages_completed_sessions(make_manager) -> None:
    manager = make_manager("alpha", "bravo", "charlie")
    manager.process_eval(
        _encrypt(_private_key("alpha")),
        decrypt_fn=_decrypt,
        score_fn=_const_score(1.0, {"session": 1.0, "client": 0.4}),
    )
    manager.process_eval(
        _encrypt(_private_key("bravo")),
        decrypt_fn=_decrypt,
        score_fn=_const_score(0.5, {"session": 0.0, "client": 0.6}),
    )

    # charlie never reported, so it contributes to neither category.
    assert manager.finalize_category_scores() == {
        "client": pytest.approx(0.5),
        "session": pytest.approx(0.5),
    }
