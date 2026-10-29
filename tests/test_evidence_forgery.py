"""B6 adversarial regressions: fabricated or insufficient evidence never passes.

Every forgery goes through the real ``/_web`` and ``/_eval`` routes. The
scorer is a sentinel that would return a passing 0.99; a forgery passes this
suite only if it never reaches it and is recorded as ``invalid``.

Previously demonstrated path (BV Fast audit, same design as v2): a raw HTTP
client with no browser fetched ``/_web`` and posted a fabricated ``/_eval``,
and the session was recorded and scored.
"""

import base64
import json
import re

import pytest
from fastapi.testclient import TestClient

from src.bv_challenge.challenge.api.main import app
from api.endpoints.challenge import evidence as ev
from api.endpoints.challenge import payload_manager as pm_mod
from api.endpoints.challenge import provenance, scoring, service
from api.endpoints.challenge.payload_manager import EvidencePolicy, PayloadManager
from api.endpoints.challenge.schemas import KeyPairPM
from api.endpoints.challenge.versioning import DEFAULT_POLICY

SECRET = b"runner-proxy-secret-" + b"x" * 24
UA = "Mozilla/5.0 (X11; Linux x86_64) Chrome/126.0"
RUNNER = {ev.RUNNER_ATTESTATION_HEADER: SECRET.decode(), ev.RUNNER_SOURCE_HEADER: "172.30.0.2",
          "user-agent": UA}
_LISTS = ("movements", "clicks", "mouseDowns", "mouseUps", "keydowns", "keyups", "scroll")
_OBJECTS = (
    "browserInfo", "runtimeIntegrity", "fingerprint", "apiAvailability",
    "collectorIntegrity", "submissionContext", "crossRealm", "errorFingerprint",
    "stackTraps", "hardware", "capabilities",
)  # fmt: skip

client = TestClient(app)


class Clock:
    def __init__(self):
        self.ms = 1_800_000_000_000

    def advance(self, ms):
        self.ms += ms


@pytest.fixture
def env(monkeypatch):
    keys = [KeyPairPM(private_key=f"private-key-{i}".ljust(40, "x"), public_key=f"public-key-{i}".ljust(40, "x"),
                      nonce=f"nonce{i}abcdef") for i in range(3)]
    monkeypatch.setattr(pm_mod.challenge_utils, "gen_key_pairs", lambda: [k.model_copy() for k in keys])
    mgr = PayloadManager()
    mgr.evidence_policy = EvidencePolicy(require_attestation=True, session_ttl_ms=300_000,
                                         max_payload_bytes=64 * 1024, version_policy=DEFAULT_POLICY)
    mgr.bind_submission("sha256:sub", "miner-1")
    mgr.pop_task()
    clock = Clock()
    monkeypatch.setattr(service, "payload_manager", mgr)
    monkeypatch.setattr(service, "_runner_secret", SECRET)
    monkeypatch.setattr(service, "_evidence_configured", True)
    monkeypatch.setattr(service.time, "time", lambda: clock.ms / 1000)
    def decrypt(ciphertext, private_key):
        plain = base64.b64decode(ciphertext).decode()
        if not plain.startswith(private_key + "|"):
            raise ValueError("wrong key")
        return plain.split("|", 1)[1]

    monkeypatch.setattr(service.challenge_utils, "decrypt", decrypt)
    calls = []

    def would_pass(data, *rest):
        calls.append(data)
        return {"score": 0.99, "score_message": "session=0.990", "hard_fail": False}

    monkeypatch.setattr(scoring, "_default_metrics_processor", would_pass)
    monkeypatch.setattr(service, "_metrics_processor", lambda: None)
    return mgr, clock, calls


def serve(headers=RUNNER):
    html = client.get("/_web", headers=headers).text
    return re.search(r"nonce[\"']?\s*[:=]\s*[\"']([A-Za-z0-9]+)", html).group(1)


def payload(mgr, nonce, *, duration_ms=8000, binding_nonce=None, **extra):
    task = mgr.tasks[nonce]
    data = {name: [] for name in _LISTS}
    data.update({name: {} for name in _OBJECTS})
    data.update(schemaVersion="3", startedAt=1000, endedAt=1000 + duration_ms,
                versions={"collector": "3.1.0", "sdk": "3.1.0"})
    data["task"] = {"seed": task["seed"], "steps": [], "completed": True,
                    "answer": {"code": task["code"].lower(), "slider": task["sliderTarget"]}}
    data["sessionBinding"] = {"nonce": binding_nonce or nonce, "schemaVersion": "3"}
    data.update(extra)
    data["sessionBinding"]["payloadHash"] = pm_mod._payload_hash(data)
    return data


def submit(mgr, nonce, data, headers=RUNNER):
    body = data if isinstance(data, str) else json.dumps(data)
    blob = base64.b64encode(f"{mgr.private_keys[nonce]}|{body}".encode()).decode()
    r = client.post("/_eval", json={"error": {"data": blob}}, headers=headers)
    assert r.status_code == 200
    return mgr.sessions[nonce]


def assert_invalid(record, calls, reason):
    assert record.completed and record.outcome == "invalid"
    assert record.evidence_status == "invalid" and reason in record.evidence_reasons
    assert record.score == 0.0 and record.hard_fail
    assert calls == []  # the would-pass scorer never ran


def test_attested_consistent_session_is_scored_and_bound(env) -> None:
    mgr, clock, calls = env
    nonce = serve()
    clock.advance(9000)
    record = submit(mgr, nonce, payload(mgr, nonce))
    assert (record.outcome, record.evidence_status, record.score) == ("scored", "valid", 0.99)
    assert len(calls) == 1


def test_raw_http_fabrication_without_the_runner_is_invalid(env) -> None:
    # The previously demonstrated path: no browser, no sandbox, direct HTTP.
    mgr, clock, calls = env
    nonce = serve(headers={"user-agent": UA})
    clock.advance(9000)
    assert_invalid(submit(mgr, nonce, payload(mgr, nonce), headers={"user-agent": UA}),
                   calls, "runner_attestation")


@pytest.mark.parametrize("forged", ["", "guess", SECRET.decode()[:-1], SECRET.decode() + "x"])
def test_forged_attestation_header_is_invalid(env, forged) -> None:
    # A header set by page JavaScript or the bot is just a guess: the proxy
    # overwrites it, and the value is never served to the miner.
    mgr, clock, calls = env
    headers = {**RUNNER, ev.RUNNER_ATTESTATION_HEADER: forged}
    nonce = serve(headers=headers)
    clock.advance(9000)
    assert_invalid(submit(mgr, nonce, payload(mgr, nonce), headers=headers), calls, "runner_attestation")


def test_attested_page_with_unattested_submission_is_invalid(env) -> None:
    mgr, clock, calls = env
    nonce = serve()
    clock.advance(9000)
    assert_invalid(submit(mgr, nonce, payload(mgr, nonce), headers={"user-agent": UA}),
                   calls, "runner_attestation")


def test_fabricated_timeline_longer_than_observed_is_invalid(env) -> None:
    mgr, clock, calls = env
    nonce = serve()
    clock.advance(1500)  # submitted 1.5 s after the page was served...
    assert_invalid(submit(mgr, nonce, payload(mgr, nonce, duration_ms=20_000)),  # ...claims 20 s
                   calls, "timing_inconsistent")


def test_missing_client_timing_is_invalid(env) -> None:
    mgr, clock, calls = env
    nonce = serve()
    clock.advance(9000)
    data = payload(mgr, nonce)
    del data["endedAt"]
    data["sessionBinding"]["payloadHash"] = pm_mod._payload_hash(data)
    assert_invalid(submit(mgr, nonce, data), calls, "timing_incomplete")


def test_submission_for_a_session_never_served_is_invalid(env) -> None:
    mgr, clock, calls = env
    serve()  # serves nonce0 only
    mgr.issue_task("nonce2abcdef")  # task known, but no page was served for it
    clock.advance(9000)
    assert_invalid(submit(mgr, "nonce2abcdef", payload(mgr, "nonce2abcdef")), calls, "session_not_served")


def test_cross_session_substitution_is_invalid(env) -> None:
    mgr, clock, calls = env
    first, second = serve(), serve()
    clock.advance(9000)
    stolen = payload(mgr, first, binding_nonce=second)  # session B's evidence under A's key
    assert_invalid(submit(mgr, first, stolen), calls, "binding_nonce")


def test_payload_hash_tampering_is_invalid(env) -> None:
    mgr, clock, calls = env
    nonce = serve()
    clock.advance(9000)
    data = payload(mgr, nonce)
    data["movements"] = [{"x": 1, "y": 1, "t": 5}]  # edited after hashing
    assert_invalid(submit(mgr, nonce, data), calls, "binding_hash")


def test_submission_from_another_container_is_invalid(env) -> None:
    mgr, clock, calls = env
    nonce = serve()
    clock.advance(9000)
    other = {**RUNNER, ev.RUNNER_SOURCE_HEADER: "172.30.0.9"}
    assert_invalid(submit(mgr, nonce, payload(mgr, nonce), headers=other), calls, "source_mismatch")


def test_submission_from_another_client_is_invalid(env) -> None:
    mgr, clock, calls = env
    nonce = serve()
    clock.advance(9000)
    other = {**RUNNER, "user-agent": "python-requests/2.32"}
    assert_invalid(submit(mgr, nonce, payload(mgr, nonce), headers=other), calls, "client_mismatch")


def test_late_submission_is_invalid(env) -> None:
    mgr, clock, calls = env
    nonce = serve()
    clock.advance(300_001)
    assert_invalid(submit(mgr, nonce, payload(mgr, nonce)), calls, "session_expired")


def test_oversized_payload_is_a_payload_hard_fail_not_a_fallback(env) -> None:
    # Audit finding: padding past the scorer's limit turned 0.0 into 0.5.
    mgr, clock, calls = env
    nonce = serve()
    clock.advance(9000)
    record = submit(mgr, nonce, payload(mgr, nonce, padding="x" * (64 * 1024)))
    assert_invalid(record, calls, "payload_too_large")
    assert record.hard_fail_reason == "payload" and record.score_source == "gate"


@pytest.mark.parametrize("body", ["{}", "[]", json.dumps({"schemaVersion": "3"})])
def test_empty_or_skeleton_payload_is_invalid(env, body) -> None:
    mgr, clock, calls = env
    nonce = serve()
    clock.advance(9000)
    record = submit(mgr, nonce, body)
    assert calls == []
    assert record.completed is (body != "[]")  # a non-object is dropped as malformed
    if record.completed:
        assert record.outcome == "invalid" and record.score == 0.0


def test_unknown_collector_version_is_invalid(env) -> None:
    mgr, clock, calls = env
    nonce = serve()
    clock.advance(9000)
    data = payload(mgr, nonce, versions={"collector": "9.9.9", "sdk": "3.1.0"})
    assert_invalid(submit(mgr, nonce, data), calls, "version_mismatch")


def test_replayed_submission_is_not_rescored(env) -> None:
    mgr, clock, calls = env
    nonce = serve()
    clock.advance(9000)
    data = payload(mgr, nonce)
    submit(mgr, nonce, data)
    clock.advance(1000)
    record = submit(mgr, nonce, data)
    assert len(calls) == 1 and record.score == 0.99


def test_scorer_receives_a_context_bound_to_this_run_and_evidence(env, monkeypatch) -> None:
    mgr, clock, calls = env
    seen = {}

    def capture(data, context):
        seen["context"] = context
        return scoring.ScoreResult(0.9, {})

    monkeypatch.setattr(service, "_score_payload", capture)
    nonce = serve()
    clock.advance(9000)
    submit(mgr, nonce, payload(mgr, nonce))
    ctx = seen["context"]
    assert (ctx.run_id, ctx.session_id, ctx.submission_id, ctx.miner_id) == (
        mgr.run_id, nonce, "sha256:sub", "miner-1")
    assert ctx.bundle["observations"]["web"]["runner_attested"] is True
    assert ctx.bundle["attestation"] == {"format": "bv-attest/1", "status": "valid", "reasons": []}
    assert provenance.digest(ctx.bundle).startswith("sha256:")
    assert ctx.versions["collector"] == "3.1.0" and ctx.versions["attestation_format"] == "bv-attest/1"


def test_without_required_attestation_consistent_evidence_is_only_review(env) -> None:
    mgr, clock, calls = env
    mgr.evidence_policy = EvidencePolicy(require_attestation=False, session_ttl_ms=300_000,
                                         max_payload_bytes=64 * 1024, version_policy=DEFAULT_POLICY)
    nonce = serve(headers={"user-agent": UA})
    clock.advance(9000)
    record = submit(mgr, nonce, payload(mgr, nonce), headers={"user-agent": UA})
    assert (record.outcome, record.evidence_status) == ("review", "review")


def test_client_supplied_runner_headers_are_ignored_without_the_secret() -> None:
    obs = ev.observe_request({ev.RUNNER_SOURCE_HEADER: "10.9.9.9", ev.RUNNER_TIME_HEADER: "1.5"},
                             now_ms=5, runner_secret=SECRET, client_host="203.0.113.7")
    assert (obs.runner_attested, obs.source, obs.runner_time_ms) == (False, "203.0.113.7", None)
    obs = ev.observe_request({**RUNNER, ev.RUNNER_TIME_HEADER: "1.5"}, now_ms=5, runner_secret=SECRET)
    assert (obs.runner_attested, obs.source, obs.runner_time_ms) == (True, "172.30.0.2", 1500)
    assert not ev.observe_request(RUNNER, now_ms=5, runner_secret=None).runner_attested
