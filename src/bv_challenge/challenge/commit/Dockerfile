FROM mcr.microsoft.com/playwright/python:v1.55.0-noble

USER root

WORKDIR /app
COPY bot.py /app/bot.py
COPY bot_runner.py /app/bot_runner.py

ENV HOME=/tmp/bv-inner4-home \
    LANG=C.UTF-8
CMD ["python3", "/app/bot_runner.py"]