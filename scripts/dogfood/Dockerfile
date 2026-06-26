# Post-release dogfood: install the PUBLISHED tensor-grep from PyPI into a clean image and run the
# full-feature battery against the REAL `tg` binary (the customer path — catches install + front-door
# routing regressions that CliRunner-based tests cannot see).
#
# Usage (run after a release confirms on PyPI):
#   docker build --build-arg TG_VERSION=1.15.1 -f scripts/dogfood/Dockerfile -t tg-dogfood scripts/dogfood
#   docker run --rm tg-dogfood
# Exit 0 = shipped artifact installs + every feature works. Exit 1 = a regression (failing command shown).
ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim

ARG TG_VERSION
RUN test -n "$TG_VERSION" || (echo "ERROR: pass --build-arg TG_VERSION=<published version>" >&2 && exit 1)

# ripgrep backs tg's text-search passthrough; install it so the real plain-text search path is exercised.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ripgrep \
    && rm -rf /var/lib/apt/lists/*

# Install exactly what a customer gets: the published wheel from PyPI.
RUN pip install --no-cache-dir "tensor-grep==${TG_VERSION}"

# Install sanity — fail the build early if the artifact didn't resolve or `tg` isn't on PATH.
RUN tg --version

COPY dogfood_features.py /dogfood/dogfood_features.py
ENTRYPOINT ["python", "/dogfood/dogfood_features.py"]
