# Accepts a desired Nautobot version as build argument, default to 2.4.5
ARG NAUTOBOT_VER="2.4.5"

# Accepts a desired Python version as build argument, default to 3.11
ARG PYTHON_VER="3.11"

# Retrieve published development image of Nautobot base which should include most CI dependencies
FROM ghcr.io/nautobot/nautobot-dev:${NAUTOBOT_VER}-py${PYTHON_VER}

# Runtime argument and environment setup
ARG NAUTOBOT_ROOT=/opt/nautobot
ENV NAUTOBOT_ROOT=${NAUTOBOT_ROOT}

COPY --from=ghcr.io/astral-sh/uv:0.13.4@sha256:9f130421e930bfbdc563c00740466603f8723158e90297d07957c2294640262b /uv /uvx /usr/local/bin/

WORKDIR /source

# Copy package metadata first; this image installs from pyproject.toml.
COPY pyproject.toml /source/

# Copy only the package inputs required for the editable install.
COPY README.md /source/
COPY nautobot_app_overlays/ /source/nautobot_app_overlays/

# Install dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --group dev -r pyproject.toml -e .

# Copy the nautobot_config.py file
COPY development/nautobot_config.py ${NAUTOBOT_ROOT}/nautobot_config.py

# Copy the entrypoint script for installing local plugins
COPY development/entrypoint.sh /opt/entrypoint.sh
RUN chmod +x /opt/entrypoint.sh

USER nautobot
