# --- Build Stage ---
FROM python:3.11-slim as builder

WORKDIR /app

# Install build dependencies (required for faiss-cpu, numpy, psycopg2-binary)
RUN apt-get update && apt-get install -y \
    build-essential \
    libopenblas-dev \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies into a virtualenv or direct
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# --- Final Stage ---
FROM python:3.11-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source code
COPY . .

# [HIGH-05] Standardize to port 8000 to match .env PORT=8000 and server.py __main__
# Previously was 8080 which caused Docker/local dev port inconsistency.
ENV PORT=8000
ENV PYTHONPATH=/app
# Production defaults (override via --env-file or ECS task definition)
ENV DEMO_MODE=false

# Create a non-root user and change ownership of the application directory
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

# Expose the server port
EXPOSE 8000

# Run the FastAPI server using uvicorn
# Use --workers 1 for Nova Sonic (bidirectional streaming requires single process)
CMD ["uvicorn", "src.server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
