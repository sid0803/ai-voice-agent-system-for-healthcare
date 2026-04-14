# --- Build Stage ---
FROM python:3.11-slim as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
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

# Set environment defaults (can be overridden at runtime)
ENV PORT=8080
ENV PYTHONPATH=/app

# Expose the server port
EXPOSE 8080

# Run the FastAPI server using uvicorn
CMD ["uvicorn", "src.server:app", "--host", "0.0.0.0", "--port", "8080"]
