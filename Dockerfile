# Pinned by digest so rebuilds are reproducible; Dependabot proposes updates.
FROM python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2

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
