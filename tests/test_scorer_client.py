"""Authoritative scorer client: verification and failure injection.

The fake scorer signs real ``bv-prov/1`` results with its own key; nothing
here depends on the private detector.
"""

import copy

import pytest
import requests

from src.bv_challenge.challenge.api.main import app  # noqa: F401  (sets up `api` imports)

from api.endpoints.challenge import provenance as prov
from api.endpoints.challenge import scorer_client as sc
from api.endpoints.challenge.versioning import DEFAULT_POLICY

NOW = 1_800_000_000
DAY = 24 * 3600
BUILD = "sha256:" + "ab" * 32


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t


class Resp:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeScorer:
    """Scripted transport. Each step: "ok", an int status, an exception, or a
    callable(result_body) -> result_body to tamper with the signed body."""

    def __init__(self, signer, steps=("ok",), version="5.0.0"):
        self.signer, self.steps, self.version = signer, list(steps), version
        self.posts = []

    def post(self, url, json, timeout):
        self.posts.append(copy.deepcopy(json))
        step = self.steps.pop(0) if self.steps else "ok"
        if isinstance(step, Exception):
            raise step
        if isinstance(step, int):
            return Resp(step, {"error": "x"})
        req = json["request"]["body"]
        body = {
            "type": "bv.scoring-result", "result_id": "res-1", "issued_at": NOW, "expires_at": NOW + 300,
            "request_digest": prov.digest(json["request"]), "evidence_digest": req["evidence_digest"],
            "run_id": req["run_id"], "session_id": req["session_id"],
            "submission_id": req["submission_id"], "miner_id": req["miner_id"],
            "scorer_version": self.version, "scorer_build_hash": BUILD, "enforcement": False,
            "decision": "scored", "score": 0.9, "score_message": "session=0.900",
            "hard_fail": False, "hard_fail_reason": None,
        }
        if callable(step):
            body = step(body)
        return Resp(200, {"result": self.signer.sign(prov.DOMAIN_SCORING_RESULT, body)})


@pytest.fixture
def keys():
    scorer = prov.Signer(prov.generate_key(), "scorer-q4", "bv-scorer")
    caller = prov.Signer(prov.generate_key(), "challenge-q4", "bv-challenge-api")
    ring = prov.Keyring.from_dict({"keys": [prov.keyring_entry(
        scorer.kid, "bv-scorer", scorer.public_key(), not_before=NOW - DAY,
        not_after=NOW + 60 * DAY, domains=[prov.DOMAIN_SCORING_RESULT])]})
    return scorer, caller, ring


def make(keys, steps=("ok",), sleeps=None, clock=None, **kw):
    scorer, caller, ring = keys
    http = kw.pop("http", None) or FakeScorer(kw.pop("signer", scorer), steps, kw.pop("version", "5.0.0"))
    client = sc.AuthoritativeScorerClient(
        "https://bv-scorer:9443", caller, ring, DEFAULT_POLICY, http=http,
        sleep=(sleeps.append if sleeps is not None else (lambda s: None)),
        clock=clock or Clock(), **kw)
    return client, http


BUNDLE = {"run_id": "run1", "session_id": "s1", "payload": {"x": 1}}


def request(client, session="s1"):
    return client.build_request(run_id="run1", session_id=session, submission_id="sub", miner_id="m",
                                bundle={**BUNDLE, "session_id": session}, versions={"page": "2.0.0"})


def test_verified_result_is_bound_to_the_request(keys) -> None:
    client, http = make(keys)
    env = request(client)
    result, signed = client.score(env, BUNDLE)
    assert result["score"] == 0.9 and result["evidence_digest"] == env["body"]["evidence_digest"]
    assert signed["signature"]["kid"] == "scorer-q4"
    assert http.posts[0]["request"] == env  # the signed request is what was sent


@pytest.mark.parametrize("tamper, code", [
    (lambda b: {**b, "session_id": "s2"}, "binding_mismatch"),        # wrong-session substitution
    (lambda b: {**b, "run_id": "run0"}, "binding_mismatch"),          # wrong-run substitution
    (lambda b: {**b, "evidence_digest": "sha256:" + "f" * 64}, "binding_mismatch"),
    (lambda b: {**b, "scorer_version": "4.0.0"}, "version_mismatch"),
    (lambda b: {**b, "scorer_build_hash": None}, "version_mismatch"),
    (lambda b: {**b, "decision": "pass"}, "bad_response"),
    (lambda b: {**b, "expires_at": NOW - 1}, "expired"),
])
def test_mismatched_or_tampered_results_are_security_errors(keys, tamper, code) -> None:
    client, http = make(keys, steps=[tamper])
    with pytest.raises(sc.ScorerSecurityError) as err:
        client.score(request(client), BUNDLE)
    assert err.value.code == code
    assert len(http.posts) == 1  # never retried


def test_stored_result_of_an_earlier_identical_request_is_an_idempotent_replay(keys) -> None:
    # The scorer returns its stored result for a duplicate (run, session,
    # evidence). Bound to the earlier request, but to the same evidence.
    client, _ = make(keys, steps=[lambda b: {**b, "request_digest": "sha256:" + "e" * 64}])
    result, _ = client.score(request(client), BUNDLE)
    assert result["idempotent_replay"] is True
    fresh, _ = make(keys)
    assert fresh.score(request(fresh), BUNDLE)[0]["idempotent_replay"] is False


def test_replayed_result_from_another_session_is_still_rejected(keys) -> None:
    client, _ = make(keys, steps=[lambda b: {**b, "request_digest": "sha256:" + "e" * 64,
                                             "session_id": "s0"}])
    with pytest.raises(sc.ScorerSecurityError) as err:
        client.score(request(client), BUNDLE)
    assert err.value.code == "binding_mismatch"


def test_result_signed_by_a_key_outside_the_keyring_is_rejected(keys) -> None:
    impostor = prov.Signer(prov.generate_key(), "scorer-q4", "bv-scorer")  # same kid, other key
    client, _ = make(keys, signer=impostor)
    with pytest.raises(sc.ScorerSecurityError) as err:
        client.score(request(client), BUNDLE)
    assert err.value.code == "bad_signature"


def test_unsigned_or_garbage_response_is_rejected(keys) -> None:
    for body in ({"score": 0.9}, ValueError("not json")):
        client, _ = make(keys, http=type("H", (), {"post": lambda self, *a, **k: Resp(200, body)})())
        with pytest.raises(sc.ScorerSecurityError):
            client.score(request(client), BUNDLE)


def test_build_hash_allowlist(keys) -> None:
    client, _ = make(keys, allowed_build_hashes={"sha256:" + "cd" * 32})
    with pytest.raises(sc.ScorerSecurityError) as err:
        client.score(request(client), BUNDLE)
    assert err.value.code == "version_mismatch"
    ok, _ = make(keys, allowed_build_hashes={BUILD})
    assert ok.score(request(ok), BUNDLE)[0]["score"] == 0.9


def test_timeouts_retry_twice_with_bounded_backoff_then_infrastructure_error(keys) -> None:
    sleeps = []
    t = requests.Timeout("slow")
    client, http = make(keys, steps=[t, t, t], sleeps=sleeps)
    with pytest.raises(sc.ScorerInfrastructureError) as err:
        client.score(request(client), BUNDLE)
    assert err.value.kind == "timeout"
    assert len(http.posts) == 3 and sleeps == [0.25, 0.5]
    assert client.timeout == 5.0


def test_retries_resend_the_same_signed_request(keys) -> None:
    client, http = make(keys, steps=[requests.ConnectionError("reset"), 503, "ok"])
    env = request(client)
    assert client.score(env, BUNDLE)[0]["score"] == 0.9
    assert len(http.posts) == 3
    assert all(p == http.posts[0] for p in http.posts)  # same nonce: idempotent key


def test_scorer_restart_mid_run_recovers(keys) -> None:
    client, _ = make(keys, steps=[requests.ConnectionError("refused"), "ok"])
    assert client.score(request(client), BUNDLE)[0]["decision"] == "scored"


@pytest.mark.parametrize("status, kind", [(401, "rejected"), (413, "rejected"), (422, "rejected")])
def test_client_errors_are_not_retried(keys, status, kind) -> None:
    client, http = make(keys, steps=[status])
    with pytest.raises(sc.ScorerInfrastructureError) as err:
        client.score(request(client), BUNDLE)
    assert err.value.kind == kind and len(http.posts) == 1


def test_conflicting_evidence_for_a_scored_session_is_a_security_error(keys) -> None:
    client, _ = make(keys, steps=[409])
    with pytest.raises(sc.ScorerSecurityError) as err:
        client.score(request(client), BUNDLE)
    assert err.value.code == "evidence_conflict"


def test_circuit_breaker_transitions() -> None:
    clock = Clock(0.0)
    breaker = sc.CircuitBreaker(threshold=3, reset_sec=30, clock=clock)
    for _ in range(2):
        breaker.record_failure()
    assert breaker.state == "closed" and breaker.allow()
    breaker.record_failure()
    assert breaker.state == "open" and not breaker.allow()
    clock.t = 31
    assert breaker.state == "half_open"
    assert breaker.allow() and not breaker.allow()  # exactly one trial call
    breaker.record_failure()  # trial failed: open again
    assert breaker.state == "open"
    clock.t = 62
    assert breaker.allow()
    breaker.record_success()
    assert breaker.state == "closed"


def test_open_circuit_fails_fast_without_calling_the_scorer(keys) -> None:
    breaker = sc.CircuitBreaker(threshold=1, reset_sec=30, clock=Clock(0.0))
    client, http = make(keys, steps=[503, 503, 503], breaker=breaker)
    with pytest.raises(sc.ScorerInfrastructureError) as err:
        client.score(request(client), BUNDLE)
    assert err.value.kind == "circuit_open" and len(http.posts) == 1
    with pytest.raises(sc.ScorerInfrastructureError):
        client.score(request(client), BUNDLE)
    assert len(http.posts) == 1


def test_retry_limit_is_capped_at_two(keys) -> None:
    with pytest.raises(ValueError):
        make(keys, max_retries=3)
