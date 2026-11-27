# syntax=docker/dockerfile:1
# Minimal distroless container image for ghcr.io/oimiragieo/tensor-grep
# Respects .dockerignore to exclude .git, target/, .venv/, and local scratch directories.

FROM rust:1.96-slim-bookworm AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    pkg-config \
    libpython3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY rust_core/ ./rust_core/
COPY src/tensor_grep/cli/commands.py ./src/tensor_grep/cli/commands.py

WORKDIR /build/rust_core
RUN cargo build --release --locked --no-default-features --bin tg

FROM gcr.io/distroless/cc-debian12:nonroot

LABEL org.opencontainers.image.title="tensor-grep" \
      org.opencontainers.image.description="Fast local code search, symbol navigation, and AI context via Rust & MCP" \
      org.opencontainers.image.source="https://github.com/oimiragieo/tensor-grep" \
      org.opencontainers.image.url="ghcr.io/oimiragieo/tensor-grep" \
      org.opencontainers.image.licenses="Apache-2.0"

COPY --from=builder /build/rust_core/target/release/tg /usr/local/bin/tg

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/tg"]
CMD ["--help"]
