# syntax=docker/dockerfile:1.7
# NVIDIA Config Manager - Unified Python Image
# Build with: docker build -t nv-config-manager .
# Run different services by changing the entrypoint
#
# Uses NVIDIA distroless Python image for minimal attack surface.
#

# =============================================================================
# Builder stage - use official uv image with Python
# =============================================================================
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca AS builder

# Use the emSign remediation release instead of the base image's bundled uv.
COPY --from=ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff /uv /uvx /usr/local/bin/

ARG APT_MIRROR_DEBIAN=""
ARG APT_MIRROR_GPG_KEY_URL=""
ARG NVCM_NUMPY_FROM_SOURCE=false
ARG NVCM_NUMPY_CPU_BASELINE=min
ARG NVCM_NUMPY_CPU_DISPATCH=max
ARG NVCM_NUMPY_ALLOW_NOBLAS=true

# Install build dependencies for native extensions (numpy, psycopg2, etc.)
# Also install openssh-server to get the moduli file and sftp binary for SFTP server
COPY --from=scripts configure-apt-mirror.sh python-ca.py /tmp/
RUN set -eux; \
    /tmp/configure-apt-mirror.sh "$APT_MIRROR_DEBIAN" "$APT_MIRROR_GPG_KEY_URL" debian && \
    apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    g++ \
    gcc \
    libffi-dev \
    libpq-dev \
    openssh-server \
    sshpass && \
    python /tmp/python-ca.py && \
    uv pip install --system 'pip>=26.2.2' && \
    rm -rf /var/lib/apt/lists/* && \
    # Stage sftp/ssh/sshpass binaries and their dependencies for multi-arch support
    mkdir -p /sftp-bin /sftp-lib && \
    cp /usr/bin/sftp /usr/bin/ssh /usr/bin/sshpass /sftp-bin/ && \
    for bin in /usr/bin/sftp /usr/bin/ssh /usr/bin/sshpass; do \
        ldd "$bin" | grep "=>" | awk '{print $3}' | xargs -I{} cp -n {} /sftp-lib/ 2>/dev/null || true; \
    done

WORKDIR /code/nv-config-manager
ARG TEMPLATE_ENGINE_VERSION=""

# Copy only the project files and directories required by the image
COPY pyproject.toml uv.lock README.md /code/nv-config-manager/
COPY src/nv_config_manager/ /code/nv-config-manager/src/nv_config_manager/
COPY src/tests/ /code/nv-config-manager/src/tests/
COPY packages/clients/ /code/nv-config-manager/packages/clients/
COPY packages/infrastructure/ /code/nv-config-manager/packages/infrastructure/
COPY packages/logging/ /code/nv-config-manager/packages/logging/
COPY packages/dcim/pyproject.toml packages/dcim/README.md /code/nv-config-manager/packages/dcim/
COPY packages/dcim/src/ /code/nv-config-manager/packages/dcim/src/
COPY plugins/dcim/nautobot-2x/pyproject.toml plugins/dcim/nautobot-2x/README.md /code/nv-config-manager/plugins/dcim/nautobot-2x/
COPY plugins/dcim/nautobot-2x/src/ /code/nv-config-manager/plugins/dcim/nautobot-2x/src/
COPY packages/templates/pyproject.toml packages/templates/README.md /code/nv-config-manager/packages/templates/
COPY packages/templates/src/ /code/nv-config-manager/packages/templates/src/
COPY packages/workflows/pyproject.toml packages/workflows/README.md /code/nv-config-manager/packages/workflows/
COPY packages/workflows/src/ /code/nv-config-manager/packages/workflows/src/
COPY db/migrations/ /code/nv-config-manager/db/migrations/
COPY db/alembic.ini /code/nv-config-manager/db/

# Create venv and install dependencies (--no-editable ensures package is in site-packages, not linked to source)
# --group integration-test includes pytest so tests can run from any nv-config-manager component
RUN uv venv /code/nv-config-manager/.venv
RUN --mount=type=cache,id=nvcm-uv-cache,target=/root/.cache/uv \
    set -eux; \
    if [ -n "$TEMPLATE_ENGINE_VERSION" ]; then \
        export SETUPTOOLS_SCM_PRETEND_VERSION="$TEMPLATE_ENGINE_VERSION"; \
    fi; \
    if [ "$NVCM_NUMPY_FROM_SOURCE" = "true" ]; then \
        uv sync \
            --frozen \
            --no-dev \
            --group integration-test \
            --no-editable \
            --no-binary-package numpy \
            --config-settings-package "numpy:setup-args=-Dcpu-baseline=${NVCM_NUMPY_CPU_BASELINE}" \
            --config-settings-package "numpy:setup-args=-Dcpu-dispatch=${NVCM_NUMPY_CPU_DISPATCH}" \
            --config-settings-package "numpy:setup-args=-Dallow-noblas=${NVCM_NUMPY_ALLOW_NOBLAS}"; \
    else \
        uv sync --frozen --no-dev --group integration-test --no-editable; \
    fi; \
    # Some dependency wheels include Git metadata that is not needed at runtime.
    find /code/nv-config-manager/.venv -name .git -prune -exec rm -rf '{}' +; \
    chmod -R a+rX /code/nv-config-manager/.venv /code/nv-config-manager/db /code/nv-config-manager/src

# =============================================================================
# Runtime stage - NVIDIA distroless Python
# =============================================================================
FROM nvcr.io/nvidia/distroless/python:3.13-v4.1.9@sha256:d9eff62906053e6eb7f55037259b281ec064bee3ab04e84f0eb92fdfd70200c0

# Replace the pinned runtime's trust store, including hashed certificate paths.
# Copying only the bundle would leave distrusted roots reachable through capath.
USER 0:0
COPY --from=scripts python-ca.py /tmp/python-ca.py
RUN ["python", "/tmp/python-ca.py", "--reset"]
COPY --from=builder /etc/ssl/certs/ /etc/ssl/certs/
COPY --from=builder /usr/share/ca-certificates/ /usr/share/ca-certificates/
# Check the actual runtime bundle, including the affected cross-sign trust anchor.
RUN ["python", "/tmp/python-ca.py"]

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /code/nv-config-manager

# Copy the virtual environment
COPY --from=builder /code/nv-config-manager/.venv /code/nv-config-manager/.venv

# Copy database migration files (needed for alembic)
COPY --from=builder /code/nv-config-manager/db /code/nv-config-manager/db

# Copy integration test files (runnable from any nv-config-manager component via python -m pytest)
COPY --from=builder /code/nv-config-manager/src/tests/__init__.py /code/nv-config-manager/src/tests/__init__.py
COPY --from=builder /code/nv-config-manager/src/tests/integration/ /code/nv-config-manager/src/tests/integration/

# Copy SSH moduli file for SFTP server (required for group exchange KEX)
COPY --from=builder /etc/ssh/moduli /etc/ssh/moduli

# Copy sftp/ssh/sshpass binaries and their dependencies for healthcheck probes
# (sftp binary is much faster than Python-based healthcheck due to no interpreter startup)
COPY --from=builder /sftp-bin/sftp /usr/bin/sftp
COPY --from=builder /sftp-bin/ssh /usr/bin/ssh
COPY --from=builder /sftp-bin/sshpass /usr/bin/sshpass
COPY --from=builder /sftp-lib/ /lib/

# Set PATH to include the venv executables
ENV PATH="/code/nv-config-manager/.venv/bin:$PATH"

# =============================================================================
# Service Entrypoints (from pyproject.toml [project.scripts])
# =============================================================================
# Override the CMD when running the container to select the service:
#
# ZTP Service:
#   docker run nv-config-manager nv-config-manager-ztp-api
#   docker run nv-config-manager nv-config-manager-ztp-sftp
#
# DHCP Service:
#   docker run nv-config-manager nv-config-manager-dhcp-confgen
#   docker run nv-config-manager nv-config-manager-dhcp-api
#
# Temporal Service:
#   docker run nv-config-manager nv-config-manager-temporal-worker
#   docker run nv-config-manager nv-config-manager-temporal-api
#   docker run nv-config-manager nv-config-manager-temporal-archive
#   docker run nv-config-manager nv-config-manager-temporal-scheduler
#   docker run nv-config-manager nv-config-manager-temporal-cli
#
# Render Service:
#   docker run nv-config-manager nv-config-manager-render-api
#   docker run nv-config-manager nv-config-manager-render-consumer
#   docker run nv-config-manager nv-config-manager-render-producer
#
# Config Store Service:
#   docker run nv-config-manager nv-config-manager-config-store-api
#   docker run nv-config-manager nv-config-manager-config-store-cache-refresh
#
# Database Migrations (Config Store):
#   docker run nv-config-manager alembic -c db/alembic.ini upgrade head
#
# Note: DHCP/Kea database schema is managed by a separate kea-admin image
# built from Dockerfile.kea-admin using the official ISC kea-admin tool.
# =============================================================================

USER nvs

# Default: start nothing (override CMD to run a service)
CMD ["python", "-c", "print('NVIDIA Config Manager - Specify a service entrypoint')"]
