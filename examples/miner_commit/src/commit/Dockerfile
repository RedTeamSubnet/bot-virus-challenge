FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium chromium-driver \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir selenium

WORKDIR /app
COPY bot /app/bot
COPY bot_runner.py /app/bot_runner.py

CMD ["python", "bot_runner.py"]