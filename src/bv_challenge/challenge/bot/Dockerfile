# syntax=docker/dockerfile:1
ARG BASE_IMAGE=selenium/standalone-chromium:latest
FROM ${BASE_IMAGE}

ARG DEBIAN_FRONTEND=noninteractive

WORKDIR /app

USER root
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        dnsutils \
        iproute2 \
        iputils-ping \
        netcat-openbsd \
        procps && \
    rm -rf /var/lib/apt/lists/* && \
    python3 -m pip install --break-system-packages --no-cache-dir selenium

COPY bot.py /app/bot.py

USER seluser

ENTRYPOINT ["python3", "/app/bot.py"]
