# WebKitGTK webview driven by a push-based message channel (PyGObject). System
# python3 (3.11) is used on purpose: the GObject-introspection typelibs bind to
# /usr/bin/python3 and the platform entrypoint invokes `python3`. Pinned amd64.
FROM --platform=linux/amd64 debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        xvfb \
        dbus \
        gir1.2-gtk-3.0 \
        gir1.2-webkit2-4.1 \
        libgl1-mesa-dri \
        python3 \
        python3-gi \
        python3-xlib \
        fonts-liberation \
        ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /root/.cache

WORKDIR /app
COPY bot /app/bot

# Overridden by the runner (entrypoint python3 + /app/bot_runner.py); kept for
# local `docker run` parity.
CMD ["python3", "/app/bot_runner.py"]