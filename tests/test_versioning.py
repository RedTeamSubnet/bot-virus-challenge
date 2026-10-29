"""One-version grace policy: upgrades, rollback, incompatible submissions."""

import pytest

from src.bv_challenge.challenge.api.main import app  # noqa: F401  (sets up `api` imports)

from api.endpoints.challenge import versioning as v

ALL = dict(v.CURRENT_VERSIONS)


def _policy(**over):
    return v.VersionPolicy.from_dict({**ALL, **over})


def test_current_versions_are_accepted() -> None:
    assert v.DEFAULT_POLICY.check(ALL) == []


def test_upgrade_keeps_exactly_one_grace_version() -> None:
    upgraded = _policy(collector=["3.2.0", "3.1.0"])
    assert upgraded.check({**ALL, "collector": "3.2.0"}) == []
    assert upgraded.check({**ALL, "collector": "3.1.0"}) == []  # grace
    assert upgraded.check({**ALL, "collector": "3.0.0"}) == ["collector"]  # N-2


def test_future_and_unknown_versions_are_rejected() -> None:
    assert v.DEFAULT_POLICY.check({**ALL, "scorer": "6.0.0"}) == ["scorer"]
    assert v.DEFAULT_POLICY.check({**ALL, "evidence_schema": "2"}) == ["evidence_schema"]


def test_missing_or_non_string_versions_are_rejected() -> None:
    partial = {k: val for k, val in ALL.items() if k != "attestation_format"}
    assert v.DEFAULT_POLICY.check(partial) == ["attestation_format"]
    assert v.DEFAULT_POLICY.check({**ALL, "task": 1}) == ["task"]


def test_rollback_policy_rejects_the_rolled_back_release() -> None:
    # Rolling back the scorer to 5.0.0 with 4.9.0 as grace: results from the
    # withdrawn 5.1.0 are refused instead of being read as 5.0.0.
    rolled_back = _policy(scorer=["5.0.0", "4.9.0"])
    assert rolled_back.check({**ALL, "scorer": "5.1.0"}) == ["scorer"]
    assert rolled_back.check({**ALL, "scorer": "4.9.0"}) == []


@pytest.mark.parametrize("bad", [[], ["1", "2", "3"]])
def test_policy_allows_at_most_one_grace_version(bad) -> None:
    with pytest.raises(ValueError):
        _policy(page=bad)


def test_policy_must_cover_every_component() -> None:
    with pytest.raises(ValueError, match="sdk"):
        v.VersionPolicy.from_dict({k: val for k, val in ALL.items() if k != "sdk"})


def test_unversioned_collector_is_labelled_explicitly_and_only_in_grace() -> None:
    legacy = v.claimed_client_versions({"schemaVersion": "3"})
    assert legacy == {"collector": v.UNVERSIONED_COLLECTOR, "sdk": v.UNVERSIONED_COLLECTOR}
    assert v.DEFAULT_POLICY.components["collector"].accepts(legacy["collector"])
    after_grace = _policy(collector="3.1.0", sdk="3.1.0")
    assert not after_grace.components["collector"].accepts(legacy["collector"])


def test_malformed_versions_block_claims_nothing() -> None:
    assert v.claimed_client_versions({"versions": "3.1.0"}) == {}
    assert v.claimed_client_versions({"versions": {"collector": "3.1.0", "sdk": "3.1.0"}}) == {
        "collector": "3.1.0", "sdk": "3.1.0",
    }
