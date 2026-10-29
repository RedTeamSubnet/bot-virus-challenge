# -*- coding: utf-8 -*-

import hashlib
import json
import math
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from api.core import utils
from api.config import config
from api.endpoints.challenge import evidence as evidence_mod
from api.endpoints.challenge import utils as challenge_utils
from api.endpoints.challenge.scoring import (
    OUTCOMES_ABORTING_THE_RUN,
    SCHEMA_VERSION,
    ScoreResult,
)
from api.endpoints.challenge.versioning import VersionPolicy, claimed_client_versions
from api.endpoints.challenge.task import (
    TASK_FAIL_REASON,
    canonicalize_element_ids,
    check_task_answer,
    generate_task,
)
from api.helpers.crypto import asymmetric as asymmetric_helper
from api.endpoints.challenge.schemas import KeyPairPM


_BINDING_VERDICT_KEYS = {
    "nonceMatch",
    "payloadHashMatch",
    "duplicate",
    "expired",
    "schemaMatch",
}


_LONE_SURROGATE = re.compile("[\ud800-\udfff]")
_ARRAY_INDEX = re.compile("0|[1-9][0-9]*")


def _js_key_order(key: str) -> tuple:
    """Sort key matching JS own-property order of a key-sorted object.

    Array-index keys ("0".."4294967294") come first in numeric order; the rest
    keep the ``Array#sort`` order (UTF-16 code units).
    """
    if _ARRAY_INDEX.fullmatch(key) and int(key) < 2**32 - 1:
        return (0, int(key), b"")
    return (1, 0, key.encode("utf-16-be", "surrogatepass"))


def _js_number(value: float) -> str:
    """Format a float exactly like JavaScript ``Number#toString`` in JSON."""
    if math.isnan(value) or math.isinf(value):
        return "null"
    if value == 0:
        return "0"
    # repr() gives the same shortest round-trip digits as JS; only the
    # placement of the decimal point / exponent differs.
    mantissa, _, exp = repr(abs(value)).partition("e")
    int_part, _, frac_part = mantissa.partition(".")
    all_digits = int_part + frac_part
    stripped = all_digits.lstrip("0")
    n = len(int_part) + int(exp or 0) - (len(all_digits) - len(stripped))
    digits = stripped.rstrip("0")
    k = len(digits)
    if k <= n <= 21:
        text = digits + "0" * (n - k)
    elif 0 < n <= 21:
        text = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        text = "0." + "0" * -n + digits
    else:
        e = n - 1
        text = (digits[0] + ("." + digits[1:] if k > 1 else "")
                + "e" + ("+" if e >= 0 else "-") + str(abs(e)))
    return "-" + text if value < 0 else text


def _canonical_json(value: Any) -> str:
    """Serialize like the browser SDK: sorted keys + ``JSON.stringify``.

    The client hashes ``JSON.stringify(canonicalize(payload))`` as UTF-8, so
    non-ASCII stays raw, lone surrogates are ``\\uXXXX`` escaped, numbers use
    JS formatting and keys follow JS property order (see ``_js_key_order``).
    """
    if isinstance(value, dict):
        keys = sorted(value, key=lambda k: _js_key_order(str(k)))
        return "{" + ",".join(
            _canonical_json(str(k)) + ":" + _canonical_json(value[k]) for k in keys
        ) + "}"
    if isinstance(value, list):
        return "[" + ",".join(_canonical_json(v) for v in value) + "]"
    if isinstance(value, str):
        return _LONE_SURROGATE.sub(
            lambda m: "\\u%04x" % ord(m.group()), json.dumps(value, ensure_ascii=False)
        )
    if isinstance(value, float):
        return _js_number(value)
    return json.dumps(value)  # bool, int, None


def _payload_hash(data: dict) -> str:
    clone = dict(data)
    binding = clone.get("sessionBinding")
    if isinstance(binding, dict):
        excluded = _BINDING_VERDICT_KEYS | {"payloadHash"}
        clone["sessionBinding"] = {
            k: v for k, v in binding.items() if k not in excluded
        }
    raw = _canonical_json(clone).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _stamp_binding_verdict(
    payload: dict,
    *,
    session_id: str,
    duplicate: bool,
) -> None:
    binding = payload.get("sessionBinding")
    if not isinstance(binding, dict):
        binding = {}
        payload["sessionBinding"] = binding

    supplied_hash = binding.get("payloadHash")
    computed_hash = _payload_hash(payload)
    binding["nonceMatch"] = binding.get("nonce") == session_id
    binding["payloadHashMatch"] = supplied_hash == computed_hash
    binding["duplicate"] = duplicate
    binding["expired"] = False
    binding["schemaMatch"] = (
        str(binding.get("schemaVersion")) == SCHEMA_VERSION
        and str(payload.get("schemaVersion")) == SCHEMA_VERSION
    )


@dataclass(frozen=True)
class EvidencePolicy:
    """B6 checks applied before a session can be scored."""

    require_attestation: bool
    session_ttl_ms: int
    max_payload_bytes: int
    version_policy: VersionPolicy
    timing_tolerance_ms: int = evidence_mod.DEFAULT_TIMING_TOLERANCE_MS


@dataclass(frozen=True)
class SessionContext:
    """What an authoritative scorer call is bound to."""

    run_id: str
    session_id: str
    submission_id: str
    miner_id: str
    bundle: Dict[str, Any]
    versions: Dict[str, str]
    evidence_status: str


# Evidence failures are gate failures with an existing coarse reason code.
def _evidence_fail_reason(reasons: List[str]) -> str:
    return "payload" if "payload_too_large" in reasons else "integrity"


@dataclass
class SessionRecord:
    session_id: str
    score: Optional[float] = None
    category_scores: Dict[str, float] = field(default_factory=dict)
    completed: bool = False
    timed_out: bool = False
    hard_fail: bool = False
    hard_fail_reason: Optional[str] = None
    score_source: Optional[str] = None
    scorer_error_type: Optional[str] = None
    outcome: Optional[str] = None
    evidence_status: Optional[str] = None
    evidence_reasons: Tuple[str, ...] = ()
    provenance: Optional[Dict[str, Any]] = None


@dataclass
class EvalOutcome:
    status: str
    session_id: Optional[str] = None
    score: Optional[float] = None
    category_scores: Dict[str, float] = field(default_factory=dict)
    hard_fail: bool = False
    hard_fail_reason: Optional[str] = None
    score_source: Optional[str] = None
    scorer_error_type: Optional[str] = None
    outcome: Optional[str] = None
    evidence_status: Optional[str] = None


class PayloadManager:
    """Own one scoring run's keys, sessions, attribution, and locks."""

    def __init__(self) -> None:
        self.run_lock = threading.Lock()
        self.claim_lock = threading.Lock()
        # None: legacy path (no server-side evidence checks). Set by the
        # service from configuration.
        self.evidence_policy: Optional[EvidencePolicy] = None
        self.reset()

    def reset(self) -> None:
        self.key_pairs: List[KeyPairPM] = challenge_utils.gen_key_pairs()
        self.run_id = utils.gen_random_string(length=16)
        self.sessions: Dict[str, SessionRecord] = {
            pair.nonce: SessionRecord(session_id=pair.nonce)
            for pair in self.key_pairs
            if pair.nonce
        }
        self.private_keys: Dict[str, str] = {
            pair.nonce: pair.private_key
            for pair in self.key_pairs
            if pair.nonce
        }
        self.cur_key_pair: Optional[KeyPairPM] = None
        # Expected task answers by session nonce. Same lifecycle as the key
        # pairs: replaced on reset(), only nonces of this run are accepted.
        self.tasks: Dict[str, Dict[str, Any]] = {}
        # What this server observed when it served /_web for each session.
        self.web_observations: Dict[str, evidence_mod.RequestObservation] = {}
        self.submission_id = "unbound"
        self.miner_id = "unbound"

    def bind_submission(self, submission_id: str, miner_id: str) -> None:
        self.submission_id = submission_id
        self.miner_id = miner_id

    def issue_task(
        self,
        nonce: str,
        observation: Optional[evidence_mod.RequestObservation] = None,
    ) -> Dict[str, Any]:
        """Generate the task for a session and remember its expected answer.

        A nonce that does not belong to this run (throwaway page) gets a task
        but nothing is stored.
        """
        task = generate_task(
            card_count_min=config.challenge.task_card_count_min,
            card_count_max=config.challenge.task_card_count_max,
            slider_tolerance=config.challenge.task_slider_tolerance,
            rotate_element_ids=config.challenge.rotate_element_ids,
        )
        with self.claim_lock:
            if nonce in self.sessions:
                self.tasks[nonce] = dict(task)
                if observation is not None:
                    self.web_observations.setdefault(nonce, observation)
        return task

    def first_hard_fail(self) -> Optional[SessionRecord]:
        """The first completed session recorded as a hard fail, if any."""
        with self.claim_lock:
            for record in self.sessions.values():
                if record.completed and record.hard_fail:
                    return record
        return None

    def pop_task(self) -> Optional[KeyPairPM]:
        if not self.key_pairs:
            self.cur_key_pair = None
            return None
        self.cur_key_pair = self.key_pairs.pop(0)
        return self.cur_key_pair

    def has_remaining_tasks(self) -> bool:
        return bool(self.key_pairs)

    def remaining_task_count(self) -> int:
        return len(self.key_pairs)

    def get_nonce(self) -> str:
        if not self.cur_key_pair or not self.cur_key_pair.public_key:
            raise ValueError("No public key is available")
        public_key = self.cur_key_pair.public_key
        self.cur_key_pair.public_key = None
        self.cur_key_pair.nonce = None
        return public_key

    def claim_web_key(self) -> Tuple[str, str, bool]:
        with self.claim_lock:
            if self.cur_key_pair and self.cur_key_pair.public_key:
                nonce = self.cur_key_pair.nonce
                public_key = self.cur_key_pair.public_key
                self.pop_task()
                return nonce, public_key, True

            nonce = utils.gen_random_string()
            public_key = asymmetric_helper.gen_key_pair(
                key_size=config.api.security.asymmetric.key_size,
                as_str=True,
            )[1]
            return nonce, public_key, False

    def completed_count(self) -> int:
        return sum(record.completed for record in self.sessions.values())

    def aborting_session(self) -> Optional[SessionRecord]:
        """A completed session whose outcome aborts the whole run, if any."""
        with self.claim_lock:
            for record in self.sessions.values():
                if record.completed and record.outcome in OUTCOMES_ABORTING_THE_RUN:
                    return record
        return None

    def evidence_counts(self) -> Dict[str, int]:
        with self.claim_lock:
            counts: Dict[str, int] = {}
            for record in self.sessions.values():
                if record.completed and record.evidence_status:
                    counts[record.evidence_status] = counts.get(record.evidence_status, 0) + 1
            return counts

    def provenance_records(self) -> List[Dict[str, Any]]:
        with self.claim_lock:
            return [
                record.provenance
                for record in self.sessions.values()
                if record.completed and record.provenance
            ]

    def fallback_count(self) -> int:
        """Completed sessions whose score is the scorer-error fallback."""
        with self.claim_lock:
            return sum(
                record.completed and record.score_source == "fallback"
                for record in self.sessions.values()
            )

    def process_eval(
        self,
        data: str,
        decrypt_fn: Callable[..., str],
        score_fn: Callable[..., ScoreResult],
        trusted_context: Optional[Dict[str, Optional[str]]] = None,
        eval_observation: Optional[evidence_mod.RequestObservation] = None,
    ) -> EvalOutcome:
        for session_id, private_key in list(self.private_keys.items()):
            try:
                plaintext = decrypt_fn(ciphertext=data, private_key=private_key)
                payload = json.loads(plaintext)
            except Exception:
                continue

            with self.claim_lock:
                record = self.sessions.get(session_id)
                if record is None or record.completed:
                    if isinstance(payload, dict):
                        _stamp_binding_verdict(
                            payload, session_id=session_id, duplicate=True
                        )
                    return EvalOutcome(status="duplicate", session_id=session_id)
                if not isinstance(payload, dict):
                    return EvalOutcome(status="malformed", session_id=session_id)
                _stamp_binding_verdict(
                    payload, session_id=session_id, duplicate=False
                )
                # After the hash check (the browser hashed the ids it was
                # served): the scorer only ever sees canonical element ids.
                canonicalize_element_ids(payload, (self.tasks.get(session_id) or {}).get("ids"))
                # This field is server-owned. Add it only after validating the
                # client payload hash, and always replace any client-supplied
                # value before the private scorer sees the payload.
                payload["_trustedRequest"] = {
                    key: value
                    for key, value in (trusted_context or {}).items()
                    if key in {"userAgent", "acceptLanguage", "secChUa"}
                    and isinstance(value, str)
                }
                result = None
                context = None
                if self.evidence_policy is not None:
                    result, context = self._check_evidence(
                        session_id, payload, plaintext, eval_observation
                    )
                # Every session must carry a correct answer to its own task,
                # whatever schema it claims; the scorer only runs after that.
                if result is not None:
                    pass
                elif not check_task_answer(self.tasks.get(session_id), payload):
                    result = ScoreResult(
                        config.challenge.gate_fail_score, {}, True, TASK_FAIL_REASON,
                        score_source="gate", outcome="invalid",
                        evidence_status=context.evidence_status if context else None,
                    )
                elif context is not None:
                    result = score_fn(payload, context)
                else:
                    result = score_fn(payload)
                record.score = result.score
                record.category_scores = result.category_scores
                record.hard_fail = bool(result.hard_fail)
                record.hard_fail_reason = (
                    result.hard_fail_reason if record.hard_fail else None
                )
                record.score_source = result.score_source
                record.scorer_error_type = result.scorer_error_type
                record.outcome = result.outcome
                record.evidence_status = result.evidence_status
                record.evidence_reasons = tuple(result.evidence_reasons)
                record.provenance = result.provenance
                record.completed = True
                return EvalOutcome(
                    status="recorded",
                    session_id=session_id,
                    score=result.score,
                    category_scores=result.category_scores,
                    hard_fail=record.hard_fail,
                    hard_fail_reason=record.hard_fail_reason,
                    score_source=record.score_source,
                    scorer_error_type=record.scorer_error_type,
                    outcome=record.outcome,
                    evidence_status=record.evidence_status,
                )

        return EvalOutcome(status="unattributable")

    def _check_evidence(
        self,
        session_id: str,
        payload: Dict[str, Any],
        plaintext: str,
        eval_observation: Optional[evidence_mod.RequestObservation],
    ) -> Tuple[Optional[ScoreResult], Optional[SessionContext]]:
        """B6: judge the payload against what the server observed.

        Returns a gate result when the evidence is invalid (never a pass),
        otherwise the context an authoritative scorer call is bound to.
        Caller holds ``claim_lock``.
        """
        policy = self.evidence_policy
        assert policy is not None
        observed = eval_observation or evidence_mod.RequestObservation(
            at_ms=0, runner_attested=False, source=None, user_agent_digest=None
        )
        web = self.web_observations.get(session_id)
        verdict = evidence_mod.assess(
            payload,
            web=web,
            eval_=observed,
            payload_bytes=len(plaintext.encode("utf-8", "surrogatepass")),
            require_attestation=policy.require_attestation,
            session_ttl_ms=policy.session_ttl_ms,
            max_payload_bytes=policy.max_payload_bytes,
            timing_tolerance_ms=policy.timing_tolerance_ms,
        )
        client_versions = claimed_client_versions(payload)
        if any(
            not policy.version_policy.components[name].accepts(client_versions.get(name))
            for name in ("collector", "sdk")
        ):
            # Client-claimed versions are miner-controlled: a mismatch makes
            # this session invalid; it must not abort (and re-run) the run.
            verdict = evidence_mod.EvidenceVerdict(
                evidence_mod.STATUS_INVALID, verdict.reasons + ["version_mismatch"]
            )
        if verdict.status == evidence_mod.STATUS_INVALID:
            return (
                ScoreResult(
                    config.challenge.gate_fail_score, {}, True,
                    _evidence_fail_reason(verdict.reasons),
                    score_source="gate", outcome="invalid",
                    evidence_status=verdict.status,
                    evidence_reasons=tuple(verdict.reasons),
                ),
                None,
            )
        bundle = evidence_mod.evidence_bundle(
            run_id=self.run_id,
            session_id=session_id,
            payload=payload,
            web=web,
            eval_=observed,
            verdict=verdict,
        )
        current = policy.version_policy.current()
        versions = {
            "page": current["page"],
            "task": current["task"],
            "evidence_schema": str(payload.get("schemaVersion")),
            "attestation_format": evidence_mod.ATTESTATION_FORMAT,
            "collector": str(client_versions.get("collector")),
            "sdk": str(client_versions.get("sdk")),
        }
        return None, SessionContext(
            run_id=self.run_id,
            session_id=session_id,
            submission_id=self.submission_id,
            miner_id=self.miner_id,
            bundle=bundle,
            versions=versions,
            evidence_status=verdict.status,
        )

    def finalize(self, timeout_score: float) -> float:
        if not self.sessions:
            return 0.0
        with self.claim_lock:
            total = 0.0
            for record in self.sessions.values():
                if record.completed and record.score is not None:
                    total += record.score
                else:
                    record.timed_out = True
                    total += timeout_score
            return total / len(self.sessions)

    def finalize_category_scores(self) -> Dict[str, float]:
        with self.claim_lock:
            totals: Dict[str, float] = {}
            counts: Dict[str, int] = {}
            for record in self.sessions.values():
                if not record.completed:
                    continue
                for category, score in record.category_scores.items():
                    totals[category] = totals.get(category, 0.0) + score
                    counts[category] = counts.get(category, 0) + 1
            return {
                category: totals[category] / counts[category]
                for category in sorted(totals)
                if counts[category]
            }


__all__ = ["EvalOutcome", "EvidencePolicy", "PayloadManager", "SessionContext", "SessionRecord"]
