#!/usr/bin/env bash
# Dry-run miner submit gates for bot_virus_v1 (no chain reveal).
set -euo pipefail

COMMIT_FILE="${BV_COMMIT_FILE:-/root/miner/volumes/configs/agent-miner/active_commit.yaml}"
PAT_FILE="${BV_PAT_FILE:-/root/miner/volumes/configs/agent-miner/personal_access_token.txt}"
DIGEST_FILE="/root/bot-virus-challenge/volumes/storage/rest-bv-challenge/data/COMMIT_DIGEST.txt"
LOCAL_IMG="${BV_LOCAL_IMAGE:-local/rest-bv-commit:fp-stable}"

echo "== commit file =="
cat "${COMMIT_FILE}"
line="$(grep -E 'bot_virus_v1---' "${COMMIT_FILE}" | head -1 | sed 's/^- //')"
[[ -n "${line}" ]] || { echo "missing bot_virus_v1 line"; exit 1; }
[[ "${line}" == *'---'* && "${line}" == *'@sha256:'* ]] || { echo "bad format: ${line}"; exit 1; }
docker_info="${line#*---}"
repo="${docker_info%@sha256:*}"
digest="sha256:${docker_info#*@sha256:}"
echo "repo=${repo}"
echo "digest=${digest}"
echo "expected_local=$(cat "${DIGEST_FILE}")"

echo "== local image =="
docker image inspect "${LOCAL_IMG}" >/dev/null
echo "local image OK: ${LOCAL_IMG}"

echo "== /solve smoke =="
cid="$(docker run -d -p 10002:10002 "${LOCAL_IMG}")"
trap 'docker rm -f "${cid}" >/dev/null 2>&1 || true' EXIT
for _ in $(seq 1 20); do curl -sf http://localhost:10002/health >/dev/null && break; sleep 0.5; done
python3 - <<'PY'
import json
from urllib.request import Request, urlopen
req=Request("http://localhost:10002/solve", data=b"{}", headers={"Content-Type":"application/json"})
with urlopen(req, timeout=30) as r:
    out=json.loads(r.read())
names={f["file_name"] for f in out["commit_files"]}
assert names=={"bot.py","Dockerfile"}
bot=next(f["content"] for f in out["commit_files"] if f["file_name"]=="bot.py")
assert "FP_PROFILES" in bot and "nodriver" in bot
print("solve OK")
PY

echo "== PAT =="
if [[ -s "${PAT_FILE}" ]]; then
	echo "PAT present ($(wc -c < "${PAT_FILE}") bytes)"
else
	echo "PAT MISSING — validators cannot pull a private Hub image until PAT is set"
fi

echo "== Hub username policy =="
if [[ "${repo}" == local/* || "${repo}" == localhost:* ]]; then
	echo "WARN: commit still points at local registry (${repo}). Run scripts/push_private.sh before mainnet reveal."
else
	echo "Hub repo looks remote: ${repo}"
fi

echo "DRY_RUN_OK"
