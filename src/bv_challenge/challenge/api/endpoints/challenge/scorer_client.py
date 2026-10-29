# -*- coding: utf-8 -*-

"""Client for the private, authoritative scorer service.

Every request is signed (``scoring-request``) and bound to the run, session,
submission, miner and evidence digest; every result must come back signed by
an allowed scorer key (``scoring-result``) and bound to the same values and to
the exact request that was sent. Anything else is rejected.

Failure semantics (DH decision):

* Transport failures, timeouts, 5xx and an open circuit raise
  ``ScorerInfrastructureError``. The run is aborted as ``infrastructure_error``;
  there is no 0.5 fallback and no silent switch to the lite scorer.
* A response that fails verification, or a scorer version outside the
  version policy, raises ``ScorerSecurityError``. That is a security event,
  not an outage, and is never retried.

Reliability baseline (provisional until latency is measured): 5 s per
attempt, at most 2 retries with bounded exponential backoff, circuit opens
after 5 consecutive failures for 30 s. Retries resend the *same* signed
request, so the scorer's idempotency key ``(run_id, session_id,
evidence_digest)`` returns the first result instead of scoring twice.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable, Iterable, Mapping

from . import provenance
from .versioning import VersionPolicy

DEFAULT_TIMEOUT_SEC = 5.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_BACKOFF_BASE_SEC = 0.25
DEFAULT_BACKOFF_MAX_SEC = 2.0
DEFAULT_BREAKER_THRESHOLD = 5
DEFAULT_BREAKER_RESET_SEC = 30.0
REQUEST_TTL_SEC = 60
SCORER_WORKLOAD = "bv-scorer"


class ScorerInfrastructureError(Exception):
    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind


class ScorerSecurityError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class CircuitBreaker:
    """closed -> open after ``threshold`` consecutive failures; after
    ``reset_sec`` one trial call is let through (half-open)."""

    def __init__(
        self,
        threshold: int = DEFAULT_BREAKER_THRESHOLD,
        reset_sec: float = DEFAULT_BREAKER_RESET_SEC,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.threshold = threshold
        self.reset_sec = reset_sec
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at: float | None = None
        self._trial_in_flight = False

    @property
    def state(self) -> str:
        with self._lock:
            return self._state()

    def _state(self) -> str:
        if self._opened_at is None:
            return "closed"
        if self._clock() - self._opened_at >= self.reset_sec:
            return "half_open"
        return "open"

    def allow(self) -> bool:
        with self._lock:
            state = self._state()
            if state == "closed":
                return True
            if state == "half_open" and not self._trial_in_flight:
                self._trial_in_flight = True
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._trial_in_flight = False

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._trial_in_flight or self._failures >= self.threshold:
                self._opened_at = self._clock()
            self._trial_in_flight = False


class AuthoritativeScorerClient:
    """Signed scoring calls with retries, a circuit breaker and verification.

    ``http`` is a ``requests.Session`` configured for mTLS (``cert`` = client
    certificate and key, ``verify`` = the internal CA bundle). It is injected
    so tests can replace the transport.
    """

    def __init__(
        self,
        url: str,
        signer: provenance.Signer,
        scorer_keyring: provenance.Keyring,
        version_policy: VersionPolicy,
        *,
        http: Any = None,
        allowed_build_hashes: Iterable[str] | None = None,
        timeout: float = DEFAULT_TIMEOUT_SEC,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base: float = DEFAULT_BACKOFF_BASE_SEC,
        backoff_max: float = DEFAULT_BACKOFF_MAX_SEC,
        breaker: CircuitBreaker | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if max_retries < 0 or max_retries > 2:
            raise ValueError("max_retries must be between 0 and 2")
        if http is None:
            import requests

            http = requests.Session()
        self.url = url.rstrip("/") + "/v2/score"
        self.signer = signer
        self.keyring = scorer_keyring
        self.version_policy = version_policy
        self.allowed_build_hashes = (
            frozenset(allowed_build_hashes) if allowed_build_hashes else None
        )
        self.http = http
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.breaker = breaker or CircuitBreaker()
        self._sleep = sleep
        self._clock = clock

    def build_request(
        self,
        *,
        run_id: str,
        session_id: str,
        submission_id: str,
        miner_id: str,
        bundle: Mapping[str, Any],
        versions: Mapping[str, str],
    ) -> dict:
        now = int(self._clock())
        return self.signer.sign(
            provenance.DOMAIN_SCORING_REQUEST,
            {
                "type": "bv.scoring-request",
                "request_id": uuid.uuid4().hex,
                "nonce": uuid.uuid4().hex,
                "issued_at": now,
                "expires_at": now + REQUEST_TTL_SEC,
                "run_id": run_id,
                "session_id": session_id,
                "submission_id": submission_id,
                "miner_id": miner_id,
                "evidence_digest": provenance.digest(bundle),
                "versions": dict(versions),
            },
        )

    def score(self, envelope: dict, bundle: Mapping[str, Any]) -> tuple[dict, dict]:
        """Send one signed request; return ``(verified result body, result envelope)``."""
        body = {"request": envelope, "evidence": bundle}
        last_error: ScorerInfrastructureError | None = None
        for attempt in range(self.max_retries + 1):
            if attempt:
                self._sleep(min(self.backoff_max, self.backoff_base * 2 ** (attempt - 1)))
            if not self.breaker.allow():
                raise ScorerInfrastructureError("circuit_open")
            try:
                response = self.http.post(self.url, json=body, timeout=self.timeout)
            except Exception as err:  # connection reset, TLS failure, timeout
                self.breaker.record_failure()
                kind = "timeout" if "timeout" in type(err).__name__.lower() else "connection"
                last_error = ScorerInfrastructureError(kind, type(err).__name__)
                continue
            status = getattr(response, "status_code", 0)
            if status >= 500 or status in (408, 429):
                self.breaker.record_failure()
                last_error = ScorerInfrastructureError("http_status", str(status))
                continue
            self.breaker.record_success()
            if status == 409:
                # Same (run, session) already scored with different evidence.
                raise ScorerSecurityError("evidence_conflict")
            if status != 200:
                # 4xx: our request was refused (auth, size, schema). Retrying
                # the same request cannot help; it is a deployment fault.
                raise ScorerInfrastructureError("rejected", str(status))
            return self._verify(response, envelope)
        assert last_error is not None
        raise last_error

    def _verify(self, response: Any, request_envelope: dict) -> tuple[dict, dict]:
        try:
            result_envelope = response.json().get("result")
        except Exception as err:
            raise ScorerSecurityError("bad_response", type(err).__name__) from err
        request = request_envelope["body"]
        try:
            result = provenance.verify(
                result_envelope,
                self.keyring,
                domain=provenance.DOMAIN_SCORING_RESULT,
                now=self._clock(),
                allowed_workloads={SCORER_WORKLOAD},
                expected={
                    "run_id": request["run_id"],
                    "session_id": request["session_id"],
                    "submission_id": request["submission_id"],
                    "miner_id": request["miner_id"],
                    "evidence_digest": request["evidence_digest"],
                },
            )
        except provenance.ProvenanceError as err:
            raise ScorerSecurityError(err.code, err.detail) from err
        # A duplicate request gets the stored result of the first request for
        # the same (run, session, evidence digest). Every other binding above
        # still holds, so it is the authoritative result for this evidence.
        result["idempotent_replay"] = result.get("request_digest") != provenance.digest(request_envelope)
        if not self.version_policy.components["scorer"].accepts(result.get("scorer_version")):
            raise ScorerSecurityError("version_mismatch", "scorer_version")
        build_hash = result.get("scorer_build_hash")
        if not isinstance(build_hash, str) or not build_hash.startswith("sha256:"):
            raise ScorerSecurityError("version_mismatch", "scorer_build_hash missing")
        if self.allowed_build_hashes is not None and build_hash not in self.allowed_build_hashes:
            raise ScorerSecurityError("version_mismatch", "scorer_build_hash not allowed")
        if result.get("decision") not in {"scored", "invalid"}:
            raise ScorerSecurityError("bad_response", "decision")
        return result, result_envelope


__all__ = [
    "AuthoritativeScorerClient",
    "CircuitBreaker",
    "ScorerInfrastructureError",
    "ScorerSecurityError",
    "SCORER_WORKLOAD",
]
