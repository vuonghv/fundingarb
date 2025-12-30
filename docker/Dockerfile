# Funding Rate Arbitrage System
# Production Docker Image

FROM python:3.11-slim-bookworm AS base

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------
# Builder stage - install Python dependencies
# -----------------------------------------------------------
FROM base AS builder

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python dependencies
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# -----------------------------------------------------------
# Production stage
# -----------------------------------------------------------
FROM base AS production

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Create non-root user
RUN groupadd -r fundingarb && \
    useradd -r -g fundingarb fundingarb && \
    mkdir -p /app/data /app/logs && \
    chown -R fundingarb:fundingarb /app

# Copy application code
COPY --chown=fundingarb:fundingarb backend/ /app/backend/
COPY --chown=fundingarb:fundingarb dashboard/ /app/dashboard/
COPY --chown=fundingarb:fundingarb config/ /app/config/

# Switch to non-root user
USER fundingarb

# Expose API port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

# Default command
CMD ["python", "-m", "backend.main"]

# -----------------------------------------------------------
# Development stage
# -----------------------------------------------------------
FROM base AS development

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install development dependencies
RUN pip install pytest pytest-asyncio pytest-cov httpx

# Copy all code
COPY . /app/

# Development command with hot reload
CMD ["uvicorn", "backend.api.server:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--reload"]
