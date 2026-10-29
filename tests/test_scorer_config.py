"""Remote (authoritative) scoring configuration fails closed."""

import pytest
from pydantic import ValidationError

from src.bv_challenge.challenge.api.main import app  # noqa: F401  (sets up `api` imports)

from api.core.configs._challenge import ChallengeConfig

BASE = {"n_run_per_ch": 10, "bot_timeout": 300, "vm_endpoint": "http://bot-runner:8000", "api_key": "k" * 16}
SIGNED = {
    "scorer_signing_key_file": "/run/secrets/challenge.pem",
    "scorer_signing_kid": "challenge-2026q4",
    "scorer_keyring_file": "/run/secrets/scorer-keyring.json",
    "runner_attestation_secret_file": "/run/secrets/runner-attestation",
}
MTLS = {
    "scorer_ca_file": "/run/secrets/ca.crt",
    "scorer_client_cert_file": "/run/secrets/client.crt",
    "scorer_client_key_file": "/run/secrets/client.key",
}


def _config(**kw):
    return ChallengeConfig(**{**BASE, **kw})


def test_https_with_signing_and_mtls_is_accepted_and_requires_attestation() -> None:
    cfg = _config(scorer_backend="remote", scorer_url="https://bv-scorer:9443", **SIGNED, **MTLS)
    assert cfg.attestation_required and cfg.scorer_timeout == 5.0 and cfg.scorer_max_retries == 2


@pytest.mark.parametrize("missing", sorted(SIGNED))
def test_remote_without_signing_material_or_attestation_is_refused(missing) -> None:
    kw = {k: v for k, v in SIGNED.items() if k != missing}
    with pytest.raises(ValidationError, match=missing):
        _config(scorer_backend="remote", scorer_url="https://bv-scorer:9443", **kw, **MTLS)


@pytest.mark.parametrize("missing", sorted(MTLS))
def test_https_without_mtls_material_is_refused(missing) -> None:
    kw = {k: v for k, v in MTLS.items() if k != missing}
    with pytest.raises(ValidationError, match="mTLS"):
        _config(scorer_backend="remote", scorer_url="https://bv-scorer:9443", **SIGNED, **kw)


def test_plain_http_needs_an_explicit_local_override() -> None:
    with pytest.raises(ValidationError, match="https"):
        _config(scorer_backend="remote", scorer_url="http://bv-scorer:9000", **SIGNED)
    assert _config(scorer_backend="remote", scorer_url="http://bv-scorer:9000",
                   scorer_allow_plain_http=True, **SIGNED).attestation_required


def test_remote_scoring_cannot_switch_attestation_off() -> None:
    with pytest.raises(ValidationError, match="attestation"):
        _config(scorer_backend="remote", scorer_url="https://bv-scorer:9443",
                require_runner_attestation=False, **SIGNED, **MTLS)


def test_auto_with_a_url_is_remote_and_dev_backends_are_not_authoritative() -> None:
    with pytest.raises(ValidationError):
        _config(scorer_backend="auto", scorer_url="https://bv-scorer:9443")
    assert not _config(scorer_backend="lite").attestation_required
    assert not _config(scorer_backend="local").attestation_required


@pytest.mark.parametrize("retries", [-1, 3])
def test_retry_budget_is_bounded(retries) -> None:
    with pytest.raises(ValidationError):
        _config(scorer_max_retries=retries)
