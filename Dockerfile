# Multi-stage Dockerfile for JasusiCLI on Render
# Stage 1: Build Rust workspace
FROM rust:1.80-slim-bookworm AS rust-builder
WORKDIR /usr/src/app

RUN apt-get update && apt-get install -y --no-install-recommends \
    pkg-config \
    libssl-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY rust/ ./rust/
WORKDIR /usr/src/app/rust
RUN cargo build --release --workspace

# Stage 2: Production runtime environment
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000 \
    HOST=0.0.0.0

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    sqlite3 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy compiled Rust binaries into system path
COPY --from=rust-builder /usr/src/app/rust/target/release/claw /usr/local/bin/claw
COPY --from=rust-builder /usr/src/app/rust/target/release/jasusi-service /usr/local/bin/jasusi-service

# Copy application files
COPY pyproject.toml README.md ./
COPY app.py settings.json ./
COPY ui/ ./ui/
COPY jasusi_cli/ ./jasusi_cli/

RUN pip install --no-cache-dir -e . uvicorn[standard]

RUN mkdir -p /root/.jasusi

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
