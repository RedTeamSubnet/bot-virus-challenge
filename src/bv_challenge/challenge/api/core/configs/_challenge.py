# -*- coding: utf-8 -*-

from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import SettingsConfigDict

from api.core.constants import ENV_PREFIX
from ._base import FrozenBaseConfig


class ChallengeConfig(FrozenBaseConfig):
    n_run_per_ch: int = Field(...)
    bot_timeout: int = Field(..., ge=1)
    # Layer 1/2 fallback score policy (not detector secrets — just policy).
    gate_fail_score: float = Field(default=0.0, ge=0.0, le=1.0)
    metrics_processor_error_score: float = Field(default=0.5, ge=0.0, le=1.0)
    session_timeout_score: float = Field(default=0.0, ge=0.0, le=1.0)
    runner_fail_score: float = Field(default=0.0, ge=0.0, le=1.0)
    # Interactive task shown on the challenge page (per session).
    task_card_count_min: int = Field(default=18, ge=6, le=200)
    task_card_count_max: int = Field(default=30, ge=6, le=200)
    task_slider_tolerance: int = Field(default=3, ge=0, le=50)
    # Random per-session DOM ids for the task elements (page reads task.ids).
    # Off by default: hardcoded ids in existing miner bots stop working when on.
    rotate_element_ids: bool = Field(default=False)
    # VM configuration for remote Docker build/run
    vm_endpoint: str = Field(...)
    vm_timeout: int = Field(default=120, ge=1)
    vm_ssl_verify: bool = Field(default=True)
    api_key: SecretStr = Field(..., min_length=8, max_length=128)
    # Where sessions are scored. "remote": the central, authoritative scorer
    # service (signed requests and results over mTLS; the detector never ships
    # with this image); "local": the rt_bv_score wheel if installed; "lite":
    # the public development scorer. Only "remote" results are authoritative;
    # "local" and "lite" are for development and are never signed.
    # "auto": remote when scorer_url is set, else the local wheel.
    scorer_backend: Literal["auto", "remote", "local", "lite"] = Field(default="auto")
    scorer_url: str | None = Field(default=None)
    # DH baseline, provisional until latency is measured: 5 s per attempt,
    # at most 2 retries with bounded exponential backoff.
    scorer_timeout: float = Field(default=5.0, gt=0.0, le=120.0)
    scorer_max_retries: int = Field(default=2, ge=0, le=2)
    # This workload's Ed25519 request-signing key and key id (mounted file).
    scorer_signing_key_file: str | None = Field(default=None)
    scorer_signing_kid: str | None = Field(default=None, max_length=128)
    # Public keyring of the scorer's result-signing keys.
    scorer_keyring_file: str | None = Field(default=None)
    # Optional allowlist of detector build hashes ("sha256:..."); empty = any
    # build whose version the version policy accepts.
    scorer_allowed_build_hashes: list[str] = Field(default_factory=list)
    # mTLS to the scorer: internal CA bundle and this workload's client cert.
    scorer_ca_file: str | None = Field(default=None)
    scorer_client_cert_file: str | None = Field(default=None)
    scorer_client_key_file: str | None = Field(default=None)
    # Plain HTTP to the scorer is refused unless explicitly allowed (local
    # Docker network only, never production).
    scorer_allow_plain_http: bool = Field(default=False)
    # Runner attestation: the secret the runner's TLS proxy adds to every
    # request it forwards (file mounted at runtime). Required for remote
    # scoring; without it every session is at best "review".
    runner_attestation_secret_file: str | None = Field(default=None)
    require_runner_attestation: bool | None = Field(default=None)
    # Decrypted payload limit. Normal payloads are about 34 KB; provisional.
    max_payload_bytes: int = Field(default=1024 * 1024, ge=64 * 1024, le=16 * 1024 * 1024)

    @property
    def attestation_required(self) -> bool:
        if self.require_runner_attestation is not None:
            return self.require_runner_attestation
        return self.scorer_backend == "remote" or (
            self.scorer_backend == "auto" and bool(self.scorer_url)
        )

    @model_validator(mode="after")
    def _check_task_card_range(self) -> "ChallengeConfig":
        if self.task_card_count_min > self.task_card_count_max:
            raise ValueError("task_card_count_min must not exceed task_card_count_max")
        return self

    @model_validator(mode="after")
    def _check_remote_scorer(self) -> "ChallengeConfig":
        remote = self.scorer_backend == "remote" or (
            self.scorer_backend == "auto" and bool(self.scorer_url)
        )
        if not remote:
            return self
        if not self.scorer_url:
            raise ValueError("scorer_backend 'remote' needs scorer_url")
        missing = [
            name
            for name in (
                "scorer_signing_key_file",
                "scorer_signing_kid",
                "scorer_keyring_file",
                "runner_attestation_secret_file",
            )
            if not getattr(self, name)
        ]
        if missing:
            raise ValueError(f"remote scoring needs {', '.join(missing)}")
        if self.require_runner_attestation is False:
            raise ValueError("remote (authoritative) scoring requires runner attestation")
        if self.scorer_url.startswith("https://"):
            if not (self.scorer_ca_file and self.scorer_client_cert_file and self.scorer_client_key_file):
                raise ValueError("https scorer_url needs scorer_ca_file and a client certificate (mTLS)")
        elif not (self.scorer_url.startswith("http://") and self.scorer_allow_plain_http):
            raise ValueError("scorer_url must be https (mTLS); plain http needs scorer_allow_plain_http")
        return self

    model_config = SettingsConfigDict(env_prefix=f"{ENV_PREFIX}CHALLENGE_")


__all__ = ["ChallengeConfig"]
