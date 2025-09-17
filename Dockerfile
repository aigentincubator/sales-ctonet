FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

# System deps for pdfminer (cryptography) and fonts
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential \
      libffi-dev \
      libssl-dev \
      ca-certificates \
      curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY python_app/ ./python_app/
COPY data/ ./data/

# Default to using the bundled data file
ENV PEPLINK_DATA_PATH=/app/data/hardware_data.json

# Gunicorn config (env overridable)
ENV GUNICORN_BIND=0.0.0.0:8000 \
    GUNICORN_WORKERS=2 \
    GUNICORN_THREADS=4 \
    GUNICORN_TIMEOUT=60

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD gunicorn -b "$GUNICORN_BIND" -w "$GUNICORN_WORKERS" --threads "$GUNICORN_THREADS" --timeout "$GUNICORN_TIMEOUT" --access-logfile - --error-logfile - python_app.app:app
