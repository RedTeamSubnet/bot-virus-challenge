# -*- coding: utf-8 -*-

"""Signed score provenance (Bot Virus ``bv-prov/1``).

Standalone on purpose: no ``api.*`` imports, so the private scorer service
carries a byte-identical copy (checked by a parity test there). Nothing in
this file is secret; private keys are only ever loaded from files mounted at
runtime, never from the image or the repository.

Envelope::

    {"format": "bv-prov/1", "domain": "<domain>", "body": {...},
     "signature": {"alg": "Ed25519", "kid": "<key id>", "sig": "<base64url>"}}

The signature covers ``format``, ``domain`` and the canonical JSON of the
whole ``body``, so a request can never be replayed as a result (domain
separation) and no single field can be swapped without breaking it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import pathlib
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

FORMAT = "bv-prov/1"
ALG = "Ed25519"

DOMAIN_SCORING_REQUEST = "scoring-request"
DOMAIN_SCORING_RESULT = "scoring-result"
DOMAIN_RUN_MANIFEST = "run-manifest"
DOMAINS = frozenset({DOMAIN_SCORING_REQUEST, DOMAIN_SCORING_RESULT, DOMAIN_RUN_MANIFEST})

# DH decision: signing/authentication keys rotate at least every 90 days. A
# keyring entry whose validity window is longer is refused outright.
MAX_KEY_VALIDITY_SEC = 90 * 24 * 3600
DEFAULT_MAX_SKEW_SEC = 30

KEY_STATUSES = frozenset({"active", "retired", "revoked"})


class ProvenanceError(Exception):
    """Verification failed. ``code`` is a stable, non-secret reason."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# --- Canonical form -----------------------------------------------------------


def canonical(value: Any) -> bytes:
    """Deterministic JSON: sorted keys, no whitespace, UTF-8, no NaN/Infinity."""
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as err:
        raise ProvenanceError("not_canonical", str(err)) from err


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def _signing_input(domain: str, body: Mapping[str, Any]) -> bytes:
    return FORMAT.encode() + b"\x00" + domain.encode() + b"\x00" + canonical(body)


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    if not isinstance(text, str):
        raise ProvenanceError("bad_format", "signature is not a string")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (ValueError, TypeError) as err:
        raise ProvenanceError("bad_format", "signature is not base64url") from err


# --- Keys ---------------------------------------------------------------------


@dataclass(frozen=True)
class KeyEntry:
    kid: str
    workload: str
    public_key: Ed25519PublicKey
    status: str
    not_before: float
    not_after: float
    domains: frozenset[str]

    def public_bytes(self) -> bytes:
        return self.public_key.public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )


class Keyring:
    """Public keys allowed to sign, by ``kid``.

    JSON form::

        {"keys": [{"kid": "...", "workload": "bv-scorer", "public_key": "<b64url raw>",
                   "status": "active|retired|revoked", "not_before": <epoch>,
                   "not_after": <epoch>, "domains": ["scoring-result"]}]}

    ``retired`` keys still verify what they signed inside their window (old
    results stay auditable); ``revoked`` keys never verify anything.
    """

    def __init__(self, entries: Iterable[KeyEntry]) -> None:
        self._keys: dict[str, KeyEntry] = {}
        for entry in entries:
            if entry.kid in self._keys:
                raise ValueError(f"duplicate kid {entry.kid!r}")
            if entry.status not in KEY_STATUSES:
                raise ValueError(f"key {entry.kid!r}: unknown status {entry.status!r}")
            if entry.not_after <= entry.not_before:
                raise ValueError(f"key {entry.kid!r}: empty validity window")
            if entry.not_after - entry.not_before > MAX_KEY_VALIDITY_SEC:
                raise ValueError(f"key {entry.kid!r}: validity exceeds 90 days")
            if not entry.domains or not entry.domains <= DOMAINS:
                raise ValueError(f"key {entry.kid!r}: bad domains")
            self._keys[entry.kid] = entry

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Keyring":
        entries = []
        for raw in data.get("keys", []):
            entries.append(
                KeyEntry(
                    kid=str(raw["kid"]),
                    workload=str(raw["workload"]),
                    public_key=Ed25519PublicKey.from_public_bytes(_b64d(raw["public_key"])),
                    status=str(raw.get("status", "active")),
                    not_before=float(raw["not_before"]),
                    not_after=float(raw["not_after"]),
                    domains=frozenset(raw["domains"]),
                )
            )
        return cls(entries)

    @classmethod
    def from_file(cls, path: str | pathlib.Path) -> "Keyring":
        return cls.from_dict(json.loads(pathlib.Path(path).read_text()))

    def get(self, kid: str) -> KeyEntry | None:
        return self._keys.get(kid)

    def to_dict(self) -> dict:
        return {
            "keys": [
                {
                    "kid": e.kid,
                    "workload": e.workload,
                    "public_key": _b64e(e.public_bytes()),
                    "status": e.status,
                    "not_before": e.not_before,
                    "not_after": e.not_after,
                    "domains": sorted(e.domains),
                }
                for e in self._keys.values()
            ]
        }


class Signer:
    """Holds one private key. Load it from a runtime-mounted file only."""

    def __init__(self, private_key: Ed25519PrivateKey, kid: str, workload: str) -> None:
        self._key = private_key
        self.kid = kid
        self.workload = workload

    @classmethod
    def from_file(cls, path: str | pathlib.Path, kid: str, workload: str) -> "Signer":
        pem = pathlib.Path(path).read_bytes()
        key = serialization.load_pem_private_key(pem, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("signing key must be Ed25519")
        return cls(key, kid, workload)

    def public_key(self) -> Ed25519PublicKey:
        return self._key.public_key()

    def sign(self, domain: str, body: Mapping[str, Any]) -> dict:
        if domain not in DOMAINS:
            raise ValueError(f"unknown domain {domain!r}")
        body = dict(body)
        body["signer"] = {"kid": self.kid, "workload": self.workload}
        sig = self._key.sign(_signing_input(domain, body))
        return {
            "format": FORMAT,
            "domain": domain,
            "body": body,
            "signature": {"alg": ALG, "kid": self.kid, "sig": _b64e(sig)},
        }


# --- Verification ---------------------------------------------------------------


def verify(
    envelope: Any,
    keyring: Keyring,
    *,
    domain: str,
    now: float | None = None,
    expected: Mapping[str, Any] | None = None,
    allowed_workloads: Iterable[str] | None = None,
    enforce_freshness: bool = True,
    max_skew_sec: float = DEFAULT_MAX_SKEW_SEC,
) -> dict:
    """Return the verified body or raise ``ProvenanceError``.

    Order: structure, key, signature, then claims. Claims are only trusted
    after the signature checks out. ``expected`` pins body fields (run id,
    session id, evidence digest, ...) so a valid result for another session
    is rejected. ``enforce_freshness=False`` is for later audits of an
    already-accepted result; the key window and revocation still apply.
    """
    now = time.time() if now is None else now
    if not isinstance(envelope, Mapping):
        raise ProvenanceError("missing_signature", "no envelope")
    if envelope.get("format") != FORMAT:
        raise ProvenanceError("bad_format", "unknown format")
    signature = envelope.get("signature")
    body = envelope.get("body")
    if not isinstance(signature, Mapping) or not signature.get("sig"):
        raise ProvenanceError("missing_signature")
    if not isinstance(body, Mapping):
        raise ProvenanceError("bad_format", "body is not an object")
    if signature.get("alg") != ALG:
        raise ProvenanceError("bad_format", "unsupported alg")
    if envelope.get("domain") != domain:
        raise ProvenanceError("wrong_domain", f"expected {domain}")

    entry = keyring.get(str(signature.get("kid")))
    if entry is None:
        raise ProvenanceError("unknown_key")
    if entry.status == "revoked":
        raise ProvenanceError("revoked_key", entry.kid)
    if domain not in entry.domains:
        raise ProvenanceError("wrong_domain", f"key {entry.kid} may not sign {domain}")
    if allowed_workloads is not None and entry.workload not in set(allowed_workloads):
        raise ProvenanceError("workload_not_allowed", entry.workload)

    try:
        entry.public_key.verify(_b64d(signature["sig"]), _signing_input(domain, body))
    except InvalidSignature as err:
        raise ProvenanceError("bad_signature") from err

    issued_at = body.get("issued_at")
    if not isinstance(issued_at, (int, float)) or isinstance(issued_at, bool):
        raise ProvenanceError("bad_format", "issued_at missing")
    if not entry.not_before <= issued_at <= entry.not_after:
        raise ProvenanceError("key_not_valid", "signed outside the key window")
    if body.get("signer") != {"kid": entry.kid, "workload": entry.workload}:
        raise ProvenanceError("binding_mismatch", "signer")

    if enforce_freshness:
        if issued_at > now + max_skew_sec:
            raise ProvenanceError("not_yet_valid")
        expires_at = body.get("expires_at")
        if not isinstance(expires_at, (int, float)) or isinstance(expires_at, bool):
            raise ProvenanceError("bad_format", "expires_at missing")
        if now > expires_at:
            raise ProvenanceError("expired")

    for key, value in (expected or {}).items():
        if body.get(key) != value:
            raise ProvenanceError("binding_mismatch", key)
    return dict(body)


class ReplayCache:
    """Single-use nonces until they expire. Thread-safe, bounded."""

    def __init__(self, max_entries: int = 100_000) -> None:
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()
        self._max = max_entries

    def check_and_store(self, nonce: str, expires_at: float, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            if len(self._seen) >= self._max:
                self._seen = {n: e for n, e in self._seen.items() if e >= now}
                if len(self._seen) >= self._max:
                    raise ProvenanceError("replay", "replay cache full")
            if nonce in self._seen and self._seen[nonce] >= now:
                raise ProvenanceError("replay", "nonce already used")
            self._seen[nonce] = expires_at

    def release(self, nonce: str) -> None:
        """Forget a nonce whose request failed before producing a result,
        so the caller's retry of the same signed request is not a replay."""
        with self._lock:
            self._seen.pop(nonce, None)


def generate_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def private_key_pem(key: Ed25519PrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def keyring_entry(
    kid: str,
    workload: str,
    public_key: Ed25519PublicKey,
    *,
    not_before: float,
    not_after: float,
    domains: Iterable[str],
    status: str = "active",
) -> dict:
    raw = public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return {
        "kid": kid,
        "workload": workload,
        "public_key": _b64e(raw),
        "status": status,
        "not_before": not_before,
        "not_after": not_after,
        "domains": sorted(domains),
    }


__all__ = [
    "FORMAT",
    "DOMAIN_SCORING_REQUEST",
    "DOMAIN_SCORING_RESULT",
    "DOMAIN_RUN_MANIFEST",
    "MAX_KEY_VALIDITY_SEC",
    "ProvenanceError",
    "KeyEntry",
    "Keyring",
    "Signer",
    "ReplayCache",
    "canonical",
    "digest",
    "verify",
    "generate_key",
    "private_key_pem",
    "keyring_entry",
]
