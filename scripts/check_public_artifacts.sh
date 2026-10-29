#!/usr/bin/env bash
set -euo pipefail


## --- Base --- ##
_SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-"$0"}")" >/dev/null 2>&1 && pwd -P)"
_PROJECT_DIR="$(cd "${_SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"
cd "${_PROJECT_DIR}" || exit 2


## --- Variables --- ##
# Docker build context of the challenge image.
_BUILD_CONTEXT="${BUILD_CONTEXT:-src/bv_challenge/challenge}"
# Wheels that are allowed to ship publicly (space separated name prefixes).
_ALLOWED_WHEELS="${ALLOWED_WHEELS:-vault_unlock-}"


## --- Main --- ##
# Generic release check: no compiled binaries, wheels, source maps or private
# keys in what this repo publishes (tracked files, untracked files, and
# git-ignored files that still land in the Docker build context).
main()
{
	local _files _file _base _allowed _prefix _failed=0

	_files="$(
		{
			git ls-files --cached --others --exclude-standard
			git ls-files --others --ignored --exclude-standard -- "${_BUILD_CONTEXT}"
		} | sort -u
	)"

	while IFS= read -r _file; do
		[ -n "${_file}" ] && [ -f "${_file}" ] || continue
		_base="$(basename -- "${_file}")"

		case "${_base}" in
			*.whl)
				_allowed=false
				for _prefix in ${_ALLOWED_WHEELS}; do
					case "${_base}" in "${_prefix}"*) _allowed=true ;; esac
				done
				if [ "${_allowed}" != true ]; then
					echo "[FAIL]: wheel not on the public allow list: ${_file}" >&2
					_failed=1
				fi
				;;
			*.so | *.so.* | *.pyd | *.dylib)
				echo "[FAIL]: compiled binary: ${_file}" >&2
				_failed=1
				;;
			*.map)
				echo "[FAIL]: source map: ${_file}" >&2
				_failed=1
				;;
		esac

		if grep -Eq -- "-----BEGIN (RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----" "${_file}" 2>/dev/null; then
			echo "[FAIL]: private key material: ${_file}" >&2
			_failed=1
		fi
		case "${_file}" in
			*.js | *.css)
				if grep -Eq "[#@] *sourceMappingURL=[^d]" "${_file}" 2>/dev/null; then
					echo "[FAIL]: source map reference: ${_file}" >&2
					_failed=1
				fi
				;;
		esac
	done <<< "${_files}"

	if [ "${_failed}" -ne 0 ]; then
		exit 1
	fi
	echo "[OK]: no forbidden public artifacts."
}

main "${@:-}"
