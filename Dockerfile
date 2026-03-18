FROM python:3.12-slim

WORKDIR /app

# Install only runtime dependencies (no OCR/extraction tools needed for API)
COPY requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt

COPY api/ api/
COPY dashboard/ dashboard/

EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
