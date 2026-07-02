# -*- coding: utf-8 -*-

"""Per-run session tracking for the scoring orchestration.

Replaces the bare global ``scores`` list with explicit run/session records so a
scoring run can be reasoned about as a unit and we can correctly handle:

  * duplicate ``/_eval`` (record a session score exactly once)
  * missing sessions / session timeout (pad to the expected count)
  * aggregation over the EXPECTED session count, not just completed ones

A session is identified by its ``session_id`` (the per-session nonce). The run
keeps each session's private key so ``/_eval`` can attribute an encrypted
payload to its session by trial-decryption, independent of arrival order.
"""

import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class SessionRecord:
    session_id: str
    score: Optional[float] = None
    completed: bool = False
    timed_out: bool = False


@dataclass
class RunStore:
    run_id: str
    expected_sessions: int
    sessions: Dict[str, SessionRecord] = field(default_factory=dict)
    # session_id -> private key PEM (kept for trial-decryption + dedup)
    private_keys: Dict[str, str] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @classmethod
    def create(cls, run_id: str, sessions: List[Tuple[str, str]]) -> "RunStore":
        """Build a run from (session_id, private_key) pairs."""
        store = cls(run_id=run_id, expected_sessions=len(sessions))
        for session_id, private_key in sessions:
            store.sessions[session_id] = SessionRecord(session_id=session_id)
            store.private_keys[session_id] = private_key
        return store

    def is_completed(self, session_id: str) -> bool:
        rec = self.sessions.get(session_id)
        return bool(rec and rec.completed)

    def get_score(self, session_id: str) -> Optional[float]:
        rec = self.sessions.get(session_id)
        return rec.score if rec else None

    def record(self, session_id: str, score: float) -> bool:
        """Record a session score exactly once.

        Returns True if recorded, False if the session is unknown or already
        completed (duplicate).
        """
        with self._lock:
            rec = self.sessions.get(session_id)
            if rec is None or rec.completed:
                return False
            rec.score = score
            rec.completed = True
            return True

    def completed_count(self) -> int:
        return sum(1 for rec in self.sessions.values() if rec.completed)

    def finalize(self, timeout_score: float) -> float:
        """Average over EXPECTED sessions; any incomplete session counts as
        ``timeout_score`` and is marked timed out."""
        if self.expected_sessions <= 0:
            return 0.0

        with self._lock:
            total = 0.0
            for rec in self.sessions.values():
                if rec.completed and rec.score is not None:
                    total += rec.score
                else:
                    rec.timed_out = True
                    total += timeout_score
            return total / self.expected_sessions


__all__ = ["SessionRecord", "RunStore"]
