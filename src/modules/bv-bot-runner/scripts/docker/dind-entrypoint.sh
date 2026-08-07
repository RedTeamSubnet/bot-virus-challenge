#!/bin/sh
set -eu

CHALLENGE_PROXY_TARGET_HOST="bot_virus_v1"
CHALLENGE_PROXY_TARGET_PORT="10001"

CHALLENGE_HTTPS_PROXY_PORT="${CHALLENGE_HTTPS_PROXY_PORT:-10443}"
TLS_CERT_DIR="${TLS_CERT_DIR:-/etc/bv-proxy/tls}"
TLS_CERT_FILE="${TLS_CERT_FILE:-${TLS_CERT_DIR}/tls.crt}"
TLS_KEY_FILE="${TLS_KEY_FILE:-${TLS_CERT_DIR}/tls.key}"
DOCKER_SOCKET_GROUP="mdm-docker"
DOCKER_SOCKET_GID="11000"

start_https_proxy() {
	mkdir -p "${TLS_CERT_DIR}" /run/nginx

	if [ ! -s "${TLS_CERT_FILE}" ] || [ ! -s "${TLS_KEY_FILE}" ]; then
		openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
			-keyout "${TLS_KEY_FILE}" \
			-out "${TLS_CERT_FILE}" \
			-subj "/CN=bot-runner-dind" \
			-addext "subjectAltName=DNS:localhost,DNS:bot-runner-dind,IP:127.0.0.1" >/dev/null 2>&1
	fi

	cat >/etc/nginx/http.d/bv-proxy.conf <<EOF
server {
	listen ${CHALLENGE_HTTPS_PROXY_PORT} ssl;
	server_name _;
	resolver 127.0.0.11 ipv6=off valid=10s;
	set \$challenge_upstream http://${CHALLENGE_PROXY_TARGET_HOST}:${CHALLENGE_PROXY_TARGET_PORT};

	ssl_certificate ${TLS_CERT_FILE};
	ssl_certificate_key ${TLS_KEY_FILE};

	location / {
		proxy_pass \$challenge_upstream;
		proxy_set_header Host \$host;
		proxy_set_header X-Forwarded-Proto https;
		proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
	}
}

EOF

	nginx
}

addgroup -g "${DOCKER_SOCKET_GID}" "${DOCKER_SOCKET_GROUP}" 2>/dev/null || true
start_https_proxy

exec dockerd-entrypoint.sh --group "${DOCKER_SOCKET_GROUP}" "$@"
