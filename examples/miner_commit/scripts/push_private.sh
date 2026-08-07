#!/usr/bin/env bash
# Push frozen BV miner commit image to a private Docker Hub repo and wire active_commit.yaml.
# Usage:
#   export BV_DOCKERHUB_USER=youruser
#   export BV_DOCKERHUB_REPO=rest-bv-commit   # optional
#   echo 'dckr_pat_...' > /root/miner/volumes/configs/agent-miner/personal_access_token.txt
#   ./scripts/push_private.sh
set -euo pipefail

_SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-"$0"}")" >/dev/null 2>&1 && pwd -P)"
_PROJECT_DIR="$(cd "${_SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"
cd "${_PROJECT_DIR}"

USER_NAME="${BV_DOCKERHUB_USER:?Set BV_DOCKERHUB_USER to your Docker Hub username}"
REPO_NAME="${BV_DOCKERHUB_REPO:-rest-bv-commit}"
TAG="${BV_DOCKERHUB_TAG:-fp-stable}"
LOCAL_SRC="${BV_LOCAL_IMAGE:-local/rest-bv-commit:fp-stable}"
PAT_FILE="${BV_PAT_FILE:-/root/miner/volumes/configs/agent-miner/personal_access_token.txt}"
COMMIT_FILE="${BV_COMMIT_FILE:-/root/miner/volumes/configs/agent-miner/active_commit.yaml}"
DIGEST_FILE="/root/bot-virus-challenge/volumes/storage/rest-bv-challenge/data/COMMIT_DIGEST.txt"

# Prefer an existing `docker login` session. The PAT file path is optional when
# BV_SKIP_DOCKER_LOGIN=1 (or when Docker Hub auth is already present).
if [[ "${BV_SKIP_DOCKER_LOGIN:-0}" == "1" ]]; then
	echo "[INFO] BV_SKIP_DOCKER_LOGIN=1 — using existing docker credentials"
elif [[ -s "${PAT_FILE}" ]]; then
	PAT="$(tr -d ' \n\r\t' < "${PAT_FILE}")"
	# Real Hub PATs are long; short/placeholder tokens would clobber a good login.
	if [[ ${#PAT} -lt 40 ]]; then
		echo "[ERROR] PAT in ${PAT_FILE} looks invalid (len=${#PAT})." >&2
		echo "        Either paste a real Hub token there, or:" >&2
		echo "          docker login -u ${USER_NAME}" >&2
		echo "          BV_SKIP_DOCKER_LOGIN=1 $0" >&2
		exit 1
	fi
	echo "${PAT}" | docker login -u "${USER_NAME}" --password-stdin
else
	echo "[ERROR] PAT file empty: ${PAT_FILE}" >&2
	echo "        Or: docker login -u ${USER_NAME} && BV_SKIP_DOCKER_LOGIN=1 $0" >&2
	exit 1
fi

REMOTE="${USER_NAME}/${REPO_NAME}:${TAG}"
docker tag "${LOCAL_SRC}" "${REMOTE}"
docker push "${REMOTE}"

DIGEST="$(docker inspect --format='{{index .RepoDigests 0}}' "${REMOTE}" | awk -F@ '{print $2}')"
echo "${DIGEST}" > "${DIGEST_FILE}"
echo "[OK] pushed ${REMOTE} @ ${DIGEST}"

# One-username policy: only bot_virus_v1 under this Hub account.
mkdir -p "$(dirname "${COMMIT_FILE}")"
cat > "${COMMIT_FILE}" <<EOF
- bot_virus_v1---${USER_NAME}/${REPO_NAME}@${DIGEST}
EOF
cp "${COMMIT_FILE}" /root/miner/templates/configs/active_commit.yaml
echo "[OK] wrote ${COMMIT_FILE}"
cat "${COMMIT_FILE}"
