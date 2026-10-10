# -*- coding: utf-8 -*-

import hashlib
import json
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from api.core import utils
from api.config import config
from api.endpoints.challenge import utils as challenge_utils
from api.endpoints.challenge.scoring import SCHEMA_VERSION, ScoreResult
from api.endpoints.challenge.task import (
    TASK_FAIL_REASON,
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


def _payload_hash(data: dict) -> str:
    clone = json.loads(json.dumps(data, sort_keys=True, separators=(",", ":")))
    binding = clone.get("sessionBinding")
    if isinstance(binding, dict):
        binding.pop("payloadHash", None)
        for key in _BINDING_VERDICT_KEYS:
            binding.pop(key, None)
    raw = json.dumps(clone, sort_keys=True, separators=(",", ":")).encode()
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


@dataclass
class SessionRecord:
    session_id: str
    score: Optional[float] = None
    category_scores: Dict[str, float] = field(default_factory=dict)
    completed: bool = False
    timed_out: bool = False
    hard_fail: bool = False
    hard_fail_reason: Optional[str] = None


@dataclass
class EvalOutcome:
    status: str
    session_id: Optional[str] = None
    score: Optional[float] = None
    category_scores: Dict[str, float] = field(default_factory=dict)
    hard_fail: bool = False
    hard_fail_reason: Optional[str] = None


class PayloadManager:
    """Own one scoring run's keys, sessions, attribution, and locks."""

    def __init__(self) -> None:
        self.run_lock = threading.Lock()
        self.claim_lock = threading.Lock()
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

    def issue_task(self, nonce: str) -> Dict[str, Any]:
        """Generate the task for a session and remember its expected answer.

        A nonce that does not belong to this run (throwaway page) gets a task
        but nothing is stored.
        """
        task = generate_task(
            card_count_min=config.challenge.task_card_count_min,
            card_count_max=config.challenge.task_card_count_max,
            slider_tolerance=config.challenge.task_slider_tolerance,
        )
        with self.claim_lock:
            if nonce in self.sessions:
                self.tasks[nonce] = dict(task)
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

    def process_eval(
        self,
        data: str,
        decrypt_fn: Callable[..., str],
        score_fn: Callable[[dict], ScoreResult],
        trusted_context: Optional[Dict[str, Optional[str]]] = None,
    ) -> EvalOutcome:
        for session_id, private_key in list(self.private_keys.items()):
            try:
                payload = json.loads(
                    decrypt_fn(ciphertext=data, private_key=private_key)
                )
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
                # This field is server-owned. Add it only after validating the
                # client payload hash, and always replace any client-supplied
                # value before the private scorer sees the payload.
                payload["_trustedRequest"] = {
                    key: value
                    for key, value in (trusted_context or {}).items()
                    if key in {"userAgent", "acceptLanguage", "secChUa"}
                    and isinstance(value, str)
                }
                # Every session must carry a correct answer to its own task,
                # whatever schema it claims; the scorer only runs after that.
                if check_task_answer(self.tasks.get(session_id), payload):
                    result = score_fn(payload)
                else:
                    result = ScoreResult(
                        config.challenge.gate_fail_score, {}, True, TASK_FAIL_REASON
                    )
                record.score = result.score
                record.category_scores = result.category_scores
                record.hard_fail = bool(result.hard_fail)
                record.hard_fail_reason = (
                    result.hard_fail_reason if record.hard_fail else None
                )
                record.completed = True
                return EvalOutcome(
                    status="recorded",
                    session_id=session_id,
                    score=result.score,
                    category_scores=result.category_scores,
                    hard_fail=record.hard_fail,
                    hard_fail_reason=record.hard_fail_reason,
                )

        return EvalOutcome(status="unattributable")

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


__all__ = ["EvalOutcome", "PayloadManager", "SessionRecord"]
