#!/usr/bin/env bash
# Ops helper: refresh cadence reminders + local re-score sanity for bot_virus_v1.
# Decay knobs from BVChallengeManager: stable_period_days=10, expiration_days=15.
set -euo pipefail

SCORE_LOG="/root/bot-virus-challenge/volumes/storage/rest-bv-challenge/data/SCORE_LOG.md"
COMMIT_FILE="${BV_COMMIT_FILE:-/root/miner/volumes/configs/agent-miner/active_commit.yaml}"
DIGEST_FILE="/root/bot-virus-challenge/volumes/storage/rest-bv-challenge/data/COMMIT_DIGEST.txt"

echo "## Bot Virus ops status $(date -u +%Y-%m-%dT%H:%MZ)"
echo
echo "### Local freeze"
tail -n 20 "${SCORE_LOG}" || true
echo
echo "### Active commit"
cat "${COMMIT_FILE}" 2>/dev/null || echo "(missing)"
echo
echo "### Digest file"
cat "${DIGEST_FILE}" 2>/dev/null || echo "(missing)"
echo
echo "### Refresh guidance"
cat <<EOF
- Re-push a new digest before day ~10 if score/similarity slips (stable_period_days=10).
- Commit expires around day 15 (expiration_days=15) — schedule a refresh earlier.
- If comparison penalty rises: rotate FP_PROFILES / motion sampling, rebuild, push_private.sh, update active_commit.yaml, restart miner.
- Similarity band: keep penalty between min_similarity and ~0.6–0.7 accept ceiling.
- Do not mix Docker Hub usernames in active_commit.yaml (one-username policy).
EOF

echo
echo "### Quick local health"
curl -sf http://localhost:10001/health && echo " challenge-api OK" || echo " challenge-api DOWN"
curl -sf http://localhost:18000/health && echo " bot-runner OK" || echo " bot-runner DOWN (optional)"
