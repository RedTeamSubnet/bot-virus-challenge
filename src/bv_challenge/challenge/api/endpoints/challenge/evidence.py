# -*- coding: utf-8 -*-

"""Server-observed evidence and runner attestation (``bv-attest/1``).

Everything in the decrypted payload is produced by a browser the miner
controls, so it can never be the only basis of a passing score. This module
adds what the miner cannot write:

* **Server-observed timing.** When this server served ``/_web`` for the
  session and when it received ``/_eval``, from its own clock.
* **Server-observed network facts.** Which client address and user agent
  made each request, and the decrypted payload size.
* **Runner attestation.** The runner's TLS proxy (outside the miner
  container, inside the sandbox host) adds ``X-BV-Runner-Attestation`` with a
  secret the miner never sees: it is added to the upstream request after the
  request leaves the miner container, and nginx replaces any value the client
  sent. Page JavaScript cannot read or forge it. A request without it did not
  come through the sandbox (for example a raw HTTP client hitting the public
  port).

``assess`` turns these into ``valid`` / ``review`` / ``invalid``. Only
``valid`` evidence can be scored as authoritative; ``invalid`` is a gate
failure and is never a pass.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from . import provenance

ATTESTATION_FORMAT = "bv-attest/1"
RUNNER_ATTESTATION_HEADER = "x-bv-runner-attestation"
RUNNER_SOURCE_HEADER = "x-bv-runner-source"
RUNNER_TIME_HEADER = "x-bv-runner-time"

STATUS_VALID = "valid"
STATUS_REVIEW = "review"
STATUS_INVALID = "invalid"

# Provisional values, pending measured baselines (see 08-dh-resubmission.md).
# Normal decrypted payloads are about 34 KB.
DEFAULT_MAX_PAYLOAD_BYTES = 1024 * 1024
# The collector starts after the page is served, so the duration it claims
# cannot exceed what the server saw. The tolerance only absorbs rounding.
DEFAULT_TIMING_TOLERANCE_MS = 1000


@dataclass(frozen=True)
class RequestObservation:
    """One request as this server saw it. Never built from payload data."""

    at_ms: int
    runner_attested: bool
    source: str | None
    user_agent_digest: str | None
    runner_time_ms: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _ua_digest(user_agent: str | None) -> str | None:
    if not user_agent:
        return None
    return "sha256:" + hashlib.sha256(user_agent.encode("utf-8", "replace")).hexdigest()[:32]


def observe_request(
    headers: Mapping[str, str],
    *,
    now_ms: int,
    runner_secret: bytes | None,
    client_host: str | None = None,
) -> RequestObservation:
    """Record a request. Runner-supplied facts count only when attested."""
    lowered = {str(k).lower(): v for k, v in headers.items()}
    presented = lowered.get(RUNNER_ATTESTATION_HEADER)
    attested = bool(
        runner_secret
        and isinstance(presented, str)
        and hmac.compare_digest(presented.encode("utf-8", "replace"), runner_secret)
    )
    source = client_host
    runner_time_ms = None
    if attested:
        # The proxy overwrites these headers; without attestation they are
        # client-controlled and ignored.
        source = lowered.get(RUNNER_SOURCE_HEADER) or client_host
        raw_time = lowered.get(RUNNER_TIME_HEADER)
        try:
            runner_time_ms = int(float(raw_time) * 1000) if raw_time else None
        except ValueError:
            runner_time_ms = None
    return RequestObservation(
        at_ms=int(now_ms),
        runner_attested=attested,
        source=source,
        user_agent_digest=_ua_digest(lowered.get("user-agent")),
        runner_time_ms=runner_time_ms,
    )


@dataclass
class EvidenceVerdict:
    status: str
    reasons: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.status == STATUS_VALID


def _claimed_duration_ms(payload: Mapping[str, Any]) -> float | None:
    started, ended = payload.get("startedAt"), payload.get("endedAt")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (started, ended)):
        return None
    return float(ended) - float(started)


def assess(
    payload: Mapping[str, Any],
    *,
    web: RequestObservation | None,
    eval_: RequestObservation,
    payload_bytes: int,
    require_attestation: bool,
    session_ttl_ms: int,
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
    timing_tolerance_ms: int = DEFAULT_TIMING_TOLERANCE_MS,
) -> EvidenceVerdict:
    """Check the payload against what the server itself observed.

    Every check is a consistency or completeness rule, not a behavioural
    threshold. ``invalid`` reasons are listed in the order found.
    """
    invalid: list[str] = []
    if payload_bytes > max_payload_bytes:
        invalid.append("payload_too_large")
    if web is None:
        invalid.append("session_not_served")

    binding = payload.get("sessionBinding")
    if not isinstance(binding, Mapping):
        invalid.append("binding_missing")
    else:
        if binding.get("nonceMatch") is not True:
            invalid.append("binding_nonce")
        if binding.get("payloadHashMatch") is not True:
            invalid.append("binding_hash")
        if binding.get("duplicate") is not False:
            invalid.append("binding_duplicate")

    attested = eval_.runner_attested and (web is not None and web.runner_attested)
    if require_attestation and not attested:
        invalid.append("runner_attestation")

    if web is not None:
        elapsed = eval_.at_ms - web.at_ms
        if elapsed <= 0:
            invalid.append("timing_order")
        elif elapsed > session_ttl_ms:
            invalid.append("session_expired")
        claimed = _claimed_duration_ms(payload)
        if claimed is None or claimed < 0:
            invalid.append("timing_incomplete")
        elif claimed > elapsed + timing_tolerance_ms:
            # The page claims more time than passed between serving it and
            # receiving its result: the timeline was fabricated.
            invalid.append("timing_inconsistent")
        if web.source != eval_.source:
            invalid.append("source_mismatch")
        if web.user_agent_digest != eval_.user_agent_digest:
            invalid.append("client_mismatch")

    if invalid:
        return EvidenceVerdict(STATUS_INVALID, invalid)
    if not attested:
        # Consistent but not proven to come from the sandbox (attestation
        # not required, i.e. development). Scored, never authoritative.
        return EvidenceVerdict(STATUS_REVIEW, ["runner_attestation_absent"])
    return EvidenceVerdict(STATUS_VALID)


def evidence_bundle(
    *,
    run_id: str,
    session_id: str,
    payload: Mapping[str, Any],
    web: RequestObservation | None,
    eval_: RequestObservation,
    verdict: EvidenceVerdict,
) -> dict:
    """What gets digested, signed over, and sent to the scorer."""
    return {
        "run_id": run_id,
        "session_id": session_id,
        "payload": dict(payload),
        "observations": {
            "web": web.to_dict() if web else None,
            "eval": eval_.to_dict(),
        },
        "attestation": {
            "format": ATTESTATION_FORMAT,
            "status": verdict.status,
            "reasons": list(verdict.reasons),
        },
    }


def evidence_digest(bundle: Mapping[str, Any]) -> str:
    return provenance.digest(bundle)


__all__ = [
    "ATTESTATION_FORMAT",
    "RUNNER_ATTESTATION_HEADER",
    "STATUS_VALID",
    "STATUS_REVIEW",
    "STATUS_INVALID",
    "RequestObservation",
    "EvidenceVerdict",
    "observe_request",
    "assess",
    "evidence_bundle",
    "evidence_digest",
]
