# -*- coding: utf-8 -*-

import json
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from api.core import utils
from api.config import config
from api.endpoints.challenge import utils as challenge_utils
from api.helpers.crypto import asymmetric as asymmetric_helper
from api.endpoints.challenge.schemas import KeyPairPM


@dataclass
class SessionRecord:
    session_id: str
    score: Optional[float] = None
    completed: bool = False
    timed_out: bool = False


@dataclass
class EvalOutcome:
    status: str
    session_id: Optional[str] = None
    score: Optional[float] = None


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
        score_fn: Callable[[dict], float],
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
                    return EvalOutcome(status="duplicate", session_id=session_id)
                score = score_fn(payload)
                record.score = score
                record.completed = True
                return EvalOutcome(
                    status="recorded",
                    session_id=session_id,
                    score=score,
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


__all__ = ["EvalOutcome", "PayloadManager", "SessionRecord"]
