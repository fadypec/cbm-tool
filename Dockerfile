# Pinned by digest so rebuilds are reproducible; Dependabot proposes updates.
FROM python:3.15.2-slim-bookworm@sha256:96599df822d0e1c6bb700014a05c7f3c8cabc6e0f7ff3c4f9f69cee033b4b5d9

WORKDIR /app

# Install only runtime dependencies (no OCR/extraction tools needed for API)
COPY requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt

COPY api/ api/
COPY dashboard/ dashboard/

EXPOSE 8000

RUN adduser --disabled-password --gecos '' appuser
USER appuser

# exec replaces the shell with uvicorn so it runs as PID 1 and receives
# SIGTERM directly from Docker/Railway for graceful shutdown.
CMD ["sh", "-c", "exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
