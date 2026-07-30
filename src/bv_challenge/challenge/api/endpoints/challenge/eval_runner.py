# -*- coding: utf-8 -*-

"""Run-scoped /_eval attribution and scoring orchestration (framework-free).

Extracted from ``service.py`` so the attribution + dedup decision and the
``/score`` orchestration can be unit-tested without the global ``TaskManager``,
FastAPI, ``config``, or the crypto/detector wheels. ``service.eval_bot`` and
``service.score`` are thin adapters that inject the real decrypt/scoring/runner
callables.

Design notes:
  * Attribution is by trial-decryption against the run's session keys -- it is
    order-independent and run-scoped, so out-of-order callbacks and stale
    callbacks from a previous run are handled without any serial pointer.
  * A payload that no current session key can decrypt is *unattributable*
    (stale/previous-run/garbage/tampered). It is ignored; it never consumes or
    advances another session. A real session that never reports is covered by
    ``RunStore.finalize`` timeout padding instead.
  * ``RunStore.record`` is the atomic source of truth for "scored exactly once",
    so a duplicate/replayed callback -- even one that races a live callback --
    cannot double-count.
"""

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Protocol, Tuple


class _Store(Protocol):
    """Minimal RunStore surface used here (duck-typed to avoid app imports)."""

    private_keys: Dict[str, str]

    def is_completed(self, session_id: str) -> bool: ...

    def record(self, session_id: str, score: float) -> bool: ...

    def finalize(self, timeout_score: float) -> float: ...


@dataclass(frozen=True)
class EvalOutcome:
    """Result of processing a single ``/_eval`` callback."""

    status: str  # "recorded" | "duplicate" | "unattributable"
    session_id: Optional[str] = None
    score: Optional[float] = None


def identify_session(
    private_keys: Dict[str, str],
    data: str,
    decrypt_fn: Callable[..., str],
    loads: Callable[[str], Any] = json.loads,
) -> Tuple[Optional[str], Optional[dict]]:
    """Attribute an encrypted payload to its session by trial-decryption.

    Returns ``(session_id, plain_data)`` for the first session key that both
    decrypts the payload and yields parseable data, else ``(None, None)``.
    Completed sessions keep their keys, so replays stay attributable here and are
    then deduped by the caller.
    """
    for _session_id, _private_key in list(private_keys.items()):
        try:
            _plaintext = decrypt_fn(ciphertext=data, private_key=_private_key)
            _plain_data = loads(_plaintext)
        except Exception:
            continue
        return _session_id, _plain_data
    return None, None


def process_eval(
    store: _Store,
    data: str,
    *,
    decrypt_fn: Callable[..., str],
    score_fn: Callable[[dict], float],
) -> EvalOutcome:
    """Attribute, dedup, and record exactly one ``/_eval`` callback.

    ``score_fn`` must never raise (it owns its own fallback); it is only invoked
    for an attributable, not-yet-completed session.
    """
    _session_id, _plain_data = identify_session(store.private_keys, data, decrypt_fn)

    if _session_id is None:
        return EvalOutcome(status="unattributable")

    if store.is_completed(_session_id):
        return EvalOutcome(status="duplicate", session_id=_session_id)

    _score = score_fn(_plain_data)

    # record() is atomic and rejects an already-completed session, so a callback
    # that raced another for the same session collapses to "duplicate" here.
    if not store.record(_session_id, _score):
        return EvalOutcome(status="duplicate", session_id=_session_id)

    return EvalOutcome(status="recorded", session_id=_session_id, score=_score)


def run_scoring(
    *,
    store: _Store,
    start_runner: Callable[[], Any],
    wait_for_completion: Callable[[], Any],
    timeout_score: float,
    runner_fail_score: float,
    on_runner_error: Optional[Callable[[Exception], Any]] = None,
) -> float:
    """Start the runner, wait for callbacks, then finalize the run score.

    If ``start_runner`` raises, returns ``runner_fail_score`` without waiting or
    touching the store. Otherwise blocks via ``wait_for_completion`` (the caller
    owns the wait policy) and averages over the EXPECTED session count, padding
    any session that never reported with ``timeout_score``.
    """
    try:
        start_runner()
    except Exception as err:
        if on_runner_error is not None:
            on_runner_error(err)
        return runner_fail_score

    wait_for_completion()
    return store.finalize(timeout_score=timeout_score)


__all__ = ["EvalOutcome", "identify_session", "process_eval", "run_scoring"]
