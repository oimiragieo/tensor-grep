FROM ubuntu:24.04@sha256:f610ab94648195aa356059f5b41d6085c9d4d903c072430cdd1af7bdb646106b

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    git=1:2.43.0-1ubuntu7.3 \
    python3=3.12.3-0ubuntu2.1 \
    python3-venv=3.12.3-0ubuntu2.1 \
    python3-pip=24.0+dfsg-1ubuntu1.3 \
    rustc=1.75.0+dfsg0ubuntu1-0ubuntu7.4 \
    cargo=1.75.0+dfsg0ubuntu1-0ubuntu7.4 \
    build-essential=12.10ubuntu1 \
    pkg-config=1.8.1-2build1 \
    libssl-dev=3.0.13-0ubuntu3.16 \
    curl=8.5.0-2ubuntu10.15 \
    ca-certificates=20260601~24.04.1 \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m pip install --break-system-packages --no-cache-dir uv==0.10.7
RUN curl --fail --location --silent --show-error https://sh.rustup.rs --output /tmp/rustup-init.sh \
    && sh /tmp/rustup-init.sh -y --profile minimal --default-toolchain 1.96.0 \
    && rm /tmp/rustup-init.sh
ENV PATH=/root/.cargo/bin:$PATH
COPY benchmarks/git_history_retrieval/harness.py /opt/checkout/benchmarks/git_history_retrieval/harness.py
COPY benchmarks/git_history_retrieval/worker.py /opt/checkout/benchmarks/git_history_retrieval/worker.py
COPY benchmarks/git_history_retrieval/Dockerfile /opt/checkout/benchmarks/git_history_retrieval/Dockerfile
COPY src/tensor_grep/core/retrieval_scoring.py /opt/checkout/src/tensor_grep/core/retrieval_scoring.py
WORKDIR /opt/checkout
ENTRYPOINT ["/usr/bin/python3", "/opt/checkout/benchmarks/git_history_retrieval/worker.py"]
