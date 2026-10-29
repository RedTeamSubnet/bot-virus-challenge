#!/usr/bin/env python3
"""Verify a Bot Virus run's signed provenance (validator side).

    python scripts/verify_provenance.py provenance.json \\
        --scorer-keyring scorer-keyring.json --challenge-keyring challenge-keyring.json

``provenance.json`` is the body of ``GET /provenance`` (API-key protected).
The keyrings hold PUBLIC keys only. Checks, in order:

1. The run manifest is signed by an allowed challenge workload key.
2. Every scorer result is signed by an allowed scorer key, and is bound to
   this run, submission and miner.
3. Each session in the manifest that claims a result has exactly that
   signed result (by digest), and its status matches the signed decision.
4. The final score is recomputed from the SIGNED results only
   (scored -> signed score or 0 on hard fail; invalid -> gate_fail_score;
   timeout -> timeout_score) and must equal the manifest's final score.

Exit 0 when everything holds, 1 otherwise. Prints a JSON report.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src/bv_challenge/challenge"))

from api.endpoints.challenge import provenance as prov  # noqa: E402

TOLERANCE = 1e-9


def verify_run(provenance: dict, scorer_keyring: prov.Keyring, challenge_keyring: prov.Keyring) -> dict:
    problems: list[str] = []
    try:
        manifest = prov.verify(
            provenance.get("manifest"), challenge_keyring, domain=prov.DOMAIN_RUN_MANIFEST,
            allowed_workloads={"bv-challenge-api"}, enforce_freshness=False,
        )
    except prov.ProvenanceError as err:
        return {"ok": False, "problems": [f"manifest: {err.code}"]}

    bound = {k: manifest[k] for k in ("run_id", "submission_id", "miner_id")}
    signed: dict[str, dict] = {}
    for envelope in provenance.get("results") or []:
        try:
            body = prov.verify(envelope, scorer_keyring, domain=prov.DOMAIN_SCORING_RESULT,
                               allowed_workloads={"bv-scorer"}, expected=bound, enforce_freshness=False)
        except prov.ProvenanceError as err:
            problems.append(f"result: {err.code} {err.detail}".strip())
            continue
        signed[prov.digest(envelope)] = body

    total, sessions = 0.0, manifest.get("sessions") or []
    used: set[str] = set()
    for session in sessions:
        status, digest = session.get("status"), session.get("result_digest")
        if status == "timeout":
            total += manifest["timeout_score"]
            continue
        body = signed.get(digest)
        if body is None:
            problems.append(f"session {session.get('session_id')}: no signed result")
            continue
        used.add(digest)
        if body["session_id"] != session.get("session_id") or body["decision"] != status:
            problems.append(f"session {session.get('session_id')}: result does not match manifest")
            continue
        if body["decision"] == "invalid":
            total += manifest["gate_fail_score"]
        else:
            total += 0.0 if body.get("hard_fail") else min(max(float(body["score"]), 0.0), 1.0)
    if set(signed) - used:
        problems.append("signed results not referenced by the manifest")
    recomputed = total / len(sessions) if sessions else 0.0
    if abs(recomputed - float(manifest["final_score"])) > TOLERANCE:
        problems.append(f"final_score {manifest['final_score']} != recomputed {recomputed}")
    if not manifest.get("authoritative"):
        problems.append("run is not authoritative")
    return {
        "ok": not problems,
        "problems": problems,
        "run_id": manifest["run_id"],
        "submission_id": manifest["submission_id"],
        "miner_id": manifest["miner_id"],
        "final_score": manifest["final_score"],
        "recomputed_score": recomputed,
        "signed_results": len(signed),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("provenance", type=pathlib.Path)
    parser.add_argument("--scorer-keyring", type=pathlib.Path, required=True)
    parser.add_argument("--challenge-keyring", type=pathlib.Path, required=True)
    args = parser.parse_args(argv)
    report = verify_run(
        json.loads(args.provenance.read_text()),
        prov.Keyring.from_file(args.scorer_keyring),
        prov.Keyring.from_file(args.challenge_keyring),
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
