# syntax=docker/dockerfile:1

FROM redteamsubnet61/bv-bot-base:latest

USER root

WORKDIR /app

ARG DEBIAN_FRONTEND=noninteractive
ARG APT_PACKAGES=""

RUN set -eux; \
    if [ -n "${APT_PACKAGES:-}" ]; then \
        apt-get update --fix-missing; \
        apt-get install -y --no-install-recommends ${APT_PACKAGES}; \
        rm -rf /var/lib/apt/lists/*; \
    fi

COPY requirements.txt /app/requirements.txt

RUN chmod 644 /app/requirements.txt && \
    ls -lah /app && \
    ls -lah /app/requirements.txt && \
    cat /app/requirements.txt

RUN python3 -m pip install --no-cache-dir --upgrade pip setuptools wheel && \
    python3 -m pip install --no-cache-dir -r /app/requirements.txt

COPY ./src /app

RUN chown -R seluser:seluser /app

USER seluser

ENTRYPOINT ["docker-entrypoint.sh"]