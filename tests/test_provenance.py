"""bv-prov/1: canonical signing, verification, key rotation, replay."""

import copy

import pytest

from src.bv_challenge.challenge.api.main import app  # noqa: F401  (sets up `api` imports)

from api.endpoints.challenge import provenance as prov

NOW = 1_800_000_000
DAY = 24 * 3600


def _signer(kid="scorer-a", workload="bv-scorer"):
    return prov.Signer(prov.generate_key(), kid, workload)


def _keyring(*entries):
    return prov.Keyring.from_dict({"keys": list(entries)})


def _entry(signer, status="active", nb=NOW - DAY, na=NOW + 60 * DAY, domains=(prov.DOMAIN_SCORING_RESULT,)):
    return prov.keyring_entry(signer.kid, signer.workload, signer.public_key(),
                              not_before=nb, not_after=na, domains=domains, status=status)


def _body(**over):
    body = {"issued_at": NOW, "expires_at": NOW + 300, "run_id": "r1", "session_id": "s1",
            "evidence_digest": "sha256:" + "0" * 64, "score": 0.9}
    body.update(over)
    return body


def _verify(env, ring, **kw):
    kw.setdefault("domain", prov.DOMAIN_SCORING_RESULT)
    kw.setdefault("now", NOW)
    return prov.verify(env, ring, **kw)


def test_canonical_form_is_order_independent_and_strict() -> None:
    assert prov.canonical({"b": 1, "a": [1, "é"]}) == prov.canonical({"a": [1, "é"], "b": 1})
    assert prov.canonical({"a": 1}) == b'{"a":1}'
    with pytest.raises(prov.ProvenanceError):
        prov.canonical({"x": float("nan")})


def test_round_trip_and_signer_identity() -> None:
    s = _signer()
    body = _verify(s.sign(prov.DOMAIN_SCORING_RESULT, _body()), _keyring(_entry(s)))
    assert body["signer"] == {"kid": "scorer-a", "workload": "bv-scorer"}


@pytest.mark.parametrize("field, value", [
    ("score", 1.0), ("run_id", "r2"), ("session_id", "s2"),
    ("evidence_digest", "sha256:" + "1" * 64), ("issued_at", NOW - 1),
])
def test_tampering_any_field_breaks_the_signature(field, value) -> None:
    s = _signer()
    env = s.sign(prov.DOMAIN_SCORING_RESULT, _body())
    env["body"][field] = value
    with pytest.raises(prov.ProvenanceError) as err:
        _verify(env, _keyring(_entry(s)))
    assert err.value.code == "bad_signature"


def test_signer_claim_cannot_be_rewritten() -> None:
    s = _signer()
    env = s.sign(prov.DOMAIN_SCORING_RESULT, _body())
    env["body"]["signer"] = {"kid": "scorer-a", "workload": "other"}
    with pytest.raises(prov.ProvenanceError):
        _verify(env, _keyring(_entry(s)))


def test_request_cannot_be_presented_as_a_result() -> None:
    s = _signer()
    ring = _keyring(_entry(s, domains=(prov.DOMAIN_SCORING_RESULT, prov.DOMAIN_SCORING_REQUEST)))
    env = s.sign(prov.DOMAIN_SCORING_REQUEST, _body())
    env["domain"] = prov.DOMAIN_SCORING_RESULT  # relabel
    with pytest.raises(prov.ProvenanceError) as err:
        _verify(env, ring)
    assert err.value.code == "bad_signature"
    with pytest.raises(prov.ProvenanceError) as err:
        _verify(s.sign(prov.DOMAIN_SCORING_REQUEST, _body()), ring)
    assert err.value.code == "wrong_domain"


def test_key_may_only_sign_its_domains() -> None:
    s = _signer()
    with pytest.raises(prov.ProvenanceError) as err:
        _verify(s.sign(prov.DOMAIN_SCORING_REQUEST, _body()), _keyring(_entry(s)),
                domain=prov.DOMAIN_SCORING_REQUEST)
    assert err.value.code == "wrong_domain"


@pytest.mark.parametrize("mutate, code", [
    (lambda e: e.pop("signature"), "missing_signature"),
    (lambda e: e["signature"].update(sig=""), "missing_signature"),
    (lambda e: e["signature"].update(sig="!!notb64"), "bad_signature"),
    (lambda e: e["signature"].update(alg="RS256"), "bad_format"),
    (lambda e: e.update(format="bv-prov/0"), "bad_format"),
    (lambda e: e["signature"].update(kid="unknown"), "unknown_key"),
])
def test_missing_or_malformed_signatures_are_rejected(mutate, code) -> None:
    s = _signer()
    env = s.sign(prov.DOMAIN_SCORING_RESULT, _body())
    mutate(env)
    with pytest.raises(prov.ProvenanceError) as err:
        _verify(env, _keyring(_entry(s)))
    assert err.value.code in {code, "bad_format"} if code == "bad_signature" else err.value.code == code


def test_no_envelope_is_rejected() -> None:
    with pytest.raises(prov.ProvenanceError) as err:
        _verify(None, _keyring())
    assert err.value.code == "missing_signature"


def test_expired_and_future_results_are_rejected_but_auditable() -> None:
    s = _signer()
    ring = _keyring(_entry(s))
    env = s.sign(prov.DOMAIN_SCORING_RESULT, _body())
    with pytest.raises(prov.ProvenanceError) as err:
        _verify(env, ring, now=NOW + 301)
    assert err.value.code == "expired"
    assert _verify(env, ring, now=NOW + 30 * DAY, enforce_freshness=False)["score"] == 0.9
    future = s.sign(prov.DOMAIN_SCORING_RESULT, _body(issued_at=NOW + 120, expires_at=NOW + 400))
    with pytest.raises(prov.ProvenanceError) as err:
        _verify(future, ring)
    assert err.value.code == "not_yet_valid"


def test_rotation_retired_key_verifies_its_window_revoked_never() -> None:
    old, new = _signer("scorer-q3"), _signer("scorer-q4")
    ring = _keyring(_entry(old, status="retired"), _entry(new))
    assert _verify(old.sign(prov.DOMAIN_SCORING_RESULT, _body()), ring)["score"] == 0.9
    assert _verify(new.sign(prov.DOMAIN_SCORING_RESULT, _body()), ring)["score"] == 0.9
    revoked = _keyring(_entry(old, status="revoked"), _entry(new))
    with pytest.raises(prov.ProvenanceError) as err:
        _verify(old.sign(prov.DOMAIN_SCORING_RESULT, _body()), revoked, enforce_freshness=False)
    assert err.value.code == "revoked_key"


def test_signature_outside_the_key_window_is_rejected() -> None:
    s = _signer()
    ring = _keyring(_entry(s, nb=NOW + DAY, na=NOW + 30 * DAY))
    with pytest.raises(prov.ProvenanceError) as err:
        _verify(s.sign(prov.DOMAIN_SCORING_RESULT, _body()), ring)
    assert err.value.code == "key_not_valid"


@pytest.mark.parametrize("bad", [
    {"na": NOW + 91 * DAY}, {"na": NOW - 2 * DAY}, {"status": "lost"}, {"domains": ("anything",)},
])
def test_keyring_rejects_bad_entries(bad) -> None:
    kw = {"nb": NOW - DAY, "na": NOW + 60 * DAY, "status": "active", "domains": (prov.DOMAIN_SCORING_RESULT,)}
    kw.update(bad)
    with pytest.raises(ValueError):
        _keyring(_entry(_signer(), **kw))


def test_duplicate_kid_is_rejected() -> None:
    s = _signer()
    with pytest.raises(ValueError):
        _keyring(_entry(s), _entry(s))


def test_expected_bindings_reject_substitution() -> None:
    s = _signer()
    env = s.sign(prov.DOMAIN_SCORING_RESULT, _body())
    with pytest.raises(prov.ProvenanceError) as err:
        _verify(env, _keyring(_entry(s)), expected={"session_id": "s2"})
    assert (err.value.code, err.value.detail) == ("binding_mismatch", "session_id")


def test_workload_allowlist() -> None:
    s = _signer(workload="bv-challenge-api")
    with pytest.raises(prov.ProvenanceError) as err:
        _verify(s.sign(prov.DOMAIN_SCORING_RESULT, _body()), _keyring(_entry(s)),
                allowed_workloads={"bv-scorer"})
    assert err.value.code == "workload_not_allowed"


def test_replay_cache_single_use_until_expiry_and_release() -> None:
    cache = prov.ReplayCache(max_entries=2)
    cache.check_and_store("n1", NOW + 60, now=NOW)
    with pytest.raises(prov.ProvenanceError):
        cache.check_and_store("n1", NOW + 60, now=NOW + 1)
    cache.release("n1")
    cache.check_and_store("n1", NOW + 60, now=NOW + 2)
    cache.check_and_store("n2", NOW + 60, now=NOW)
    with pytest.raises(prov.ProvenanceError):  # full of live nonces: fail closed
        cache.check_and_store("n3", NOW + 60, now=NOW)
    cache.check_and_store("n3", NOW + 200, now=NOW + 120)  # expired ones evicted


def test_keyring_round_trips_and_loads_private_keys_from_files(tmp_path) -> None:
    key = prov.generate_key()
    path = tmp_path / "signing.pem"
    path.write_bytes(prov.private_key_pem(key))
    s = prov.Signer.from_file(path, "k1", "bv-scorer")
    ring = _keyring(_entry(s))
    again = prov.Keyring.from_dict(copy.deepcopy(ring.to_dict()))
    assert _verify(s.sign(prov.DOMAIN_SCORING_RESULT, _body()), again)["score"] == 0.9
