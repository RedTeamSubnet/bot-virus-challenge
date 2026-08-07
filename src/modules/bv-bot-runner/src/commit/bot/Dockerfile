FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:99 \
    CHROME_BIN=/usr/bin/chromium

# Drop Chrome's paired mousemove stream (pointermove kept). A/B: activity unchanged.
ENV BV_MOVE_DEDUP=suppress_mouse

# CDP-dark: scored move+click is 100% xdotool; nodriver for nav/bounds/spoof.
# Wheel stays CDP (A/B: full X11 wheel cut activity 0.936→0.864).
ENV BV_CDP_DARK=1

# nodriver: official successor to undetected-chromedriver (raw CDP, no Selenium).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        chromium \
        xvfb \
        x11-utils \
        xdotool \
        fonts-liberation \
        fonts-dejavu-core \
        ca-certificates \
        libnss3 \
        libatk-bridge2.0-0 \
        libgtk-3-0 \
        libx11-xcb1 \
        libxcb-dri3-0 \
        libdrm2 \
        libgbm1 \
        libasound2 \
        libegl1 \
        libgles2 \
        libgl1-mesa-dri \
        mesa-utils \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir "nodriver==0.50.3"

WORKDIR /app
COPY bot /app/bot
COPY bot_runner.py /app/bot_runner.py

# Large virtual desktop so each session can use a different Chrome window size.
CMD ["sh", "-c", "Xvfb :99 -screen 0 2560x1440x24 -ac +extension RANDR >/tmp/xvfb.log 2>&1 & sleep 1 && exec python bot_runner.py"]
