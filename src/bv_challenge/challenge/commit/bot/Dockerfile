FROM --platform=linux/amd64 debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        dbus \
        fonts-liberation \
        gir1.2-gtk-3.0 \
        gir1.2-webkit2-4.1 \
        libgl1-mesa-dri \
        python3 \
        python3-gi \
        xvfb \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /root/.cache

WORKDIR /app
COPY bot /app/bot

CMD ["python3", "/app/bot_runner.py"]