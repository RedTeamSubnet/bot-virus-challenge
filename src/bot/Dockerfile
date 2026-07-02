# syntax=docker/dockerfile:1
# Reference bot image — two-file contract: this Dockerfile + bot.py.
FROM redteamsubnet61/bv-bot-base:latest

WORKDIR /app

# The base image ships Chrome and a Python venv (/opt/venv) on PATH, but not the
# Selenium Python client. Install it into that venv as root (the venv's
# site-packages is not writable by the default seluser).
USER root
RUN /opt/venv/bin/python3 -m pip install --no-cache-dir selenium

COPY bot.py /app/bot.py

# Drop back to the unprivileged default user to run the bot.
USER seluser

ENTRYPOINT ["python3", "/app/bot.py"]
