# Pinned by digest so rebuilds are reproducible; Dependabot proposes updates.
FROM python:3.15.0-slim-bookworm@sha256:0e019bbdfb5d9874cb2389f1c2884b64aa2ac97c99960b5bc521cef978c0617d

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
