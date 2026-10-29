"""Validator-side verification of what the service publishes."""

import copy
import importlib.util
import pathlib

import pytest

from src.bv_challenge.challenge.api.main import app  # noqa: F401  (sets up `api` imports)

from api.endpoints.challenge import payload_manager as pm_mod
from api.endpoints.challenge import provenance as prov
from api.endpoints.challenge import service
from api.endpoints.challenge.payload_manager import PayloadManager
from api.endpoints.challenge.schemas import KeyPairPM

NOW = 1_800_000_000
DAY = 24 * 3600
_spec = importlib.util.spec_from_file_location(
    "verify_provenance", pathlib.Path(__file__).parents[1] / "scripts/verify_provenance.py")
verifier = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verifier)


def _ring(signer, domain):
    return prov.Keyring.from_dict({"keys": [prov.keyring_entry(
        signer.kid, signer.workload, signer.public_key(), not_before=NOW - DAY,
        not_after=NOW + 60 * DAY, domains=[domain])]})


@pytest.fixture
def run(monkeypatch):
    keys = [KeyPairPM(private_key=f"pk-{i}".ljust(40, "x"), public_key=f"pub-{i}".ljust(40, "x"),
                      nonce=f"nonce{i}abcdef") for i in range(4)]
    monkeypatch.setattr(pm_mod.challenge_utils, "gen_key_pairs", lambda: keys)
    monkeypatch.setattr(prov.time, "time", lambda: NOW)
    monkeypatch.setattr(service.time, "time", lambda: NOW)
    mgr = PayloadManager()
    mgr.bind_submission("sha256:sub", "miner-7")
    scorer = prov.Signer(prov.generate_key(), "scorer-q4", "bv-scorer")
    challenge = prov.Signer(prov.generate_key(), "challenge-q4", "bv-challenge-api")

    def result(session, decision="scored", score=0.8, run_id=None):
        body = {"type": "bv.scoring-result", "issued_at": NOW, "expires_at": NOW + 300,
                "run_id": run_id or mgr.run_id, "session_id": session, "submission_id": "sha256:sub",
                "miner_id": "miner-7", "evidence_digest": "sha256:e", "request_digest": "sha256:r",
                "scorer_version": "5.0.0", "scorer_build_hash": "sha256:b", "decision": decision,
                "score": score if decision == "scored" else None, "hard_fail": decision != "scored"}
        return scorer.sign(prov.DOMAIN_SCORING_RESULT, body)

    plan = {"nonce0abcdef": ("scored", 0.8), "nonce1abcdef": ("scored", 0.6),
            "nonce2abcdef": ("invalid", None)}  # nonce3 times out
    for sid, (decision, score) in plan.items():
        rec = mgr.sessions[sid]
        rec.completed, rec.provenance = True, result(sid, decision, score)
        rec.score = score if decision == "scored" else 0.0
    monkeypatch.setattr(service, "payload_manager", mgr)
    monkeypatch.setattr(service, "_signer", challenge)
    service._publish_provenance(mgr.finalize(timeout_score=0.0))
    return (service.get_provenance(), _ring(scorer, prov.DOMAIN_SCORING_RESULT),
            _ring(challenge, prov.DOMAIN_RUN_MANIFEST), challenge, result)


def test_published_provenance_verifies_and_recomputes(run) -> None:
    published, scorer_ring, challenge_ring, *_ = run
    report = verifier.verify_run(published, scorer_ring, challenge_ring)
    assert report["ok"], report["problems"]
    assert report["recomputed_score"] == pytest.approx((0.8 + 0.6 + 0.0 + 0.0) / 4)
    assert report["miner_id"] == "miner-7" and report["signed_results"] == 3


def test_challenge_cannot_inflate_the_final_score(run) -> None:
    published, scorer_ring, challenge_ring, challenge, _ = run
    body = copy.deepcopy(published["manifest"]["body"])
    body["final_score"] = 0.95
    forged = {**published, "manifest": challenge.sign(prov.DOMAIN_RUN_MANIFEST, body)}
    report = verifier.verify_run(forged, scorer_ring, challenge_ring)
    assert not report["ok"] and "recomputed" in report["problems"][0]


def test_edited_manifest_breaks_its_signature(run) -> None:
    published, scorer_ring, challenge_ring, *_ = run
    forged = copy.deepcopy(published)
    forged["manifest"]["body"]["final_score"] = 0.95
    assert verifier.verify_run(forged, scorer_ring, challenge_ring)["problems"] == ["manifest: bad_signature"]


def test_result_from_another_run_is_rejected(run) -> None:
    published, scorer_ring, challenge_ring, _, result = run
    forged = copy.deepcopy(published)
    forged["results"][0] = result("nonce0abcdef", run_id="other-run")
    report = verifier.verify_run(forged, scorer_ring, challenge_ring)
    assert not report["ok"] and any("binding_mismatch" in p for p in report["problems"])


def test_missing_result_and_upgraded_status_are_rejected(run) -> None:
    published, scorer_ring, challenge_ring, challenge, _ = run
    dropped = {**published, "results": published["results"][1:]}
    assert not verifier.verify_run(dropped, scorer_ring, challenge_ring)["ok"]
    body = copy.deepcopy(published["manifest"]["body"])
    body["sessions"][2]["status"] = "scored"  # claim the invalid session was scored
    relabelled = {**published, "manifest": challenge.sign(prov.DOMAIN_RUN_MANIFEST, body)}
    assert not verifier.verify_run(relabelled, scorer_ring, challenge_ring)["ok"]


def test_manifest_from_a_non_challenge_key_is_rejected(run) -> None:
    published, scorer_ring, challenge_ring, *_ = run
    stranger = prov.Signer(prov.generate_key(), "challenge-q4", "bv-challenge-api")
    forged = {**published, "manifest": stranger.sign(prov.DOMAIN_RUN_MANIFEST, published["manifest"]["body"])}
    assert verifier.verify_run(forged, scorer_ring, challenge_ring)["problems"] == ["manifest: bad_signature"]


def test_cli_exit_codes(run, tmp_path) -> None:
    import json

    published, scorer_ring, challenge_ring, *_ = run
    (tmp_path / "p.json").write_text(json.dumps(published))
    (tmp_path / "s.json").write_text(json.dumps(scorer_ring.to_dict()))
    (tmp_path / "c.json").write_text(json.dumps(challenge_ring.to_dict()))
    args = [str(tmp_path / "p.json"), "--scorer-keyring", str(tmp_path / "s.json"),
            "--challenge-keyring", str(tmp_path / "c.json")]
    assert verifier.main(args) == 0
    published["results"] = []
    (tmp_path / "p.json").write_text(json.dumps(published))
    assert verifier.main(args) == 1
