#!/bin/bash
set -euo pipefail


# Getting path of this script file:
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
_PROJECT_DIR="$(cd "${_SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"
cd "${_PROJECT_DIR}" || exit 2

# Loading .env file (if exists):
if [ -f ".env" ]; then
	# shellcheck disable=SC1091
	source .env
fi


docker run \
	--rm \
	-it \
	--platform linux/amd64 \
	--name bot_container \
	-e BV_WEB_URL="${BV_WEB_URL:-http://172.17.0.1:10001/_web}" \
	-e BV_ACTION_LIST="${BV_ACTION_LIST:-}" \
	-e BV_SESSION_COUNT="${BV_SESSION_COUNT:-}" \
	bot:latest

	# --network bot-virus-challenge_default \
	# -e BV_WEB_URL="${BV_WEB_URL:-http://challenger-api:10001/_web}" \
