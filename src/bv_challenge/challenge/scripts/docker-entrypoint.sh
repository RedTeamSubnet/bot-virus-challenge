#!/usr/bin/env bash
set -euo pipefail


echo "[INFO]: Running '${BV_CHALLENGE_API_SLUG}' docker-entrypoint.sh..."


_seed_commit_dir()
{
	# The bot build context is a volume shared with the DinD daemon, because it
	# is the daemon, not this container, that resolves the bot_runner.py bind
	# mount into the bot. Both sides therefore mount it at the same path. Seed
	# it from the image on every start: it deliberately is not the repo
	# checkout, so a scoring run can no longer dirty the working tree.
	local _commit_dir="${BV_CHALLENGE_COMMIT_DIR:-/commit}"
	echo "[INFO]: Seeding bot build context at '${_commit_dir}'..."
	mkdir -p "${_commit_dir}/bot" || exit 2
	cp -f "${BV_CHALLENGE_API_DIR}/commit/bot_runner.py" "${_commit_dir}/bot_runner.py" || exit 2
	chown -R "${USER}:${GROUP}" "${_commit_dir}" || exit 2
	chmod 770 "${_commit_dir}" "${_commit_dir}/bot" || exit 2
	# Readable by whatever user the miner image runs as, which is not ours.
	chmod 644 "${_commit_dir}/bot_runner.py" || exit 2
}


_run()
{
	_seed_commit_dir

	# Run as the app user, so this proves the identity that will actually use
	# the socket can reach it, not just that root can.
	echo "[INFO]: Waiting for the Docker daemon..."
	gosu "${USER}:${GROUP}" python -m api.endpoints.challenge.docker_client || exit 2

	echo "[INFO]: Starting FastAPI server..."
	exec gosu "${USER}:${GROUP}" python -m api || exit 2
	# exec gosu "${USER}:${GROUP}" uvicorn api.main:app \
	# 	--host=0.0.0.0 \
	# 	--port=${BV_CHALLENGE_API_PORT:-10001} \
	# 	--no-access-log \
	# 	--no-server-header \
	# 	--proxy-headers \
	# 	--forwarded-allow-ips='*' || exit 2
	exit 0
}


main()
{
	umask 0002 || exit 2

	find "${BV_CHALLENGE_HOME_DIR}" \
		"${BV_CHALLENGE_API_CONFIGS_DIR}" \
		"${BV_CHALLENGE_API_DATA_DIR}" \
		"${BV_CHALLENGE_API_LOGS_DIR}" \
		"${BV_CHALLENGE_API_TMP_DIR}" \
		\( \
			-type d -name ".git" -o \
			-type d -name ".venv" -o \
			-type d -name "venv" -o \
			-type d -name "env" -o \
			-type d -name "modules" -o \
			-type d -name "volumes" -o \
			-type l -name ".env" \
		\) -prune -o -print0 | \
			xargs -0 chown -c "${USER}:${GROUP}" || exit 2

	find "${BV_CHALLENGE_API_DIR}" "${BV_CHALLENGE_API_CONFIGS_DIR}" "${BV_CHALLENGE_API_DATA_DIR}" \
		\( \
			-type d -name ".git" -o \
			-type d -name ".venv" -o \
			-type d -name "venv" -o \
			-type d -name "env" -o \
			-type d -name "scripts" -o \
			-type d -name "modules" -o \
			-type d -name "volumes" \
		 \) -prune -o -type d -exec \
			chmod 770 {} + || exit 2

	find "${BV_CHALLENGE_API_DIR}" "${BV_CHALLENGE_API_CONFIGS_DIR}" "${BV_CHALLENGE_API_DATA_DIR}" \
		\( \
			-type d -name ".git" -o \
			-type d -name ".venv" -o \
			-type d -name "venv" -o \
			-type d -name "env" -o \
			-type d -name "scripts" -o \
			-type d -name "modules" -o \
			-type d -name "volumes" -o \
			-type l -name ".env" \
		\) -prune -o -type f -exec \
			chmod 660 {} + || exit 2

	find "${BV_CHALLENGE_API_DIR}" "${BV_CHALLENGE_API_CONFIGS_DIR}" "${BV_CHALLENGE_API_DATA_DIR}" \
		\( \
			-type d -name ".git" -o \
			-type d -name ".venv" -o \
			-type d -name "venv" -o \
			-type d -name "env" -o \
			-type d -name "scripts" -o \
			-type d -name "modules" -o \
			-type d -name "volumes" \
		\) -prune -o -type d -exec \
			chmod ug+s {} + || exit 2

	find "${BV_CHALLENGE_API_LOGS_DIR}" "${BV_CHALLENGE_API_TMP_DIR}" -type d -exec chmod 775 {} + || exit 2
	find "${BV_CHALLENGE_API_LOGS_DIR}" "${BV_CHALLENGE_API_TMP_DIR}" -type f -exec chmod 664 {} + || exit 2
	find "${BV_CHALLENGE_API_LOGS_DIR}" "${BV_CHALLENGE_API_TMP_DIR}" -type d -exec chmod +s {} + || exit 2

	# echo "${USER} ALL=(ALL) ALL" | tee -a "/etc/sudoers.d/${USER}" > /dev/null || exit 2
	echo ""

	## Parsing input:
	case ${1:-} in
		"" | -s | --start | start | --run | run)
			_run;;
			# shift;;
		-b | --bash | bash | /bin/bash)
			shift
			if [ -z "${*:-}" ]; then
				echo "[INFO]: Starting bash..."
				exec gosu "${USER}:${GROUP}" /bin/bash
			else
				echo "[INFO]: Executing command -> ${*}"
				exec gosu "${USER}:${GROUP}" /bin/bash -c "$@" || exit 2
			fi
			exit 0;;
		*)
			echo "[ERROR]: Failed to parsing input -> ${*}!" >&2
			echo "[INFO]: USAGE: ${0}  -s, --start, start | -b, --bash, bash, /bin/bash"
			exit 1;;
	esac
}

main "$@"
