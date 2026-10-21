# syntax=docker/dockerfile:1.7
# Test image for running the vendored nv_config_manager plugin's Django test
# suite. Derived from the production runtime image with test-only deps added.
#
# Not used in production or kind deployments -- only by `make test-nautobot-plugin`.
#
# Two-stage so we end up with a runnable image (the production runtime is
# distroless and has no pip/shell, so we copy the venv into a uv-capable image
# and install the extra deps there).

ARG BASE_TAG=latest
FROM nv-config-manager-nautobot:${BASE_TAG} AS app

FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim@sha256:4f5d923c9dcea037f57bda425dd209f3ec643da2f0b74227f68d09dab0b3bb36 AS test

# Use the emSign remediation release instead of the base image's bundled uv.
COPY --from=ghcr.io/astral-sh/uv:0.13.4@sha256:9f130421e930bfbdc563c00740466603f8723158e90297d07957c2294640262b /uv /uvx /usr/local/bin/

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    && \
    dpkg --compare-versions "$(dpkg-query -W -f='${Version}' ca-certificates)" ge '20250419~deb12u2' && \
    uv pip install --system 'pip>=26.2.2' && \
    rm -rf /var/lib/apt/lists/*

COPY --from=app /opt/nautobot /opt/nautobot
COPY --from=app /usr/lib/libpq.so* /usr/lib/
COPY --from=app /usr/lib/libxml2.so* /usr/lib/
COPY --from=app /usr/lib/libxslt.so* /usr/lib/
COPY --from=app /usr/lib/libexslt.so* /usr/lib/
COPY --from=app /usr/lib/libldap*.so* /usr/lib/
COPY --from=app /usr/lib/liblber*.so* /usr/lib/
COPY --from=app /usr/lib/libsasl2.so* /usr/lib/
COPY --from=app /usr/lib/libjpeg.so* /usr/lib/

RUN --mount=type=cache,id=nvcm-uv-cache,target=/root/.cache/uv \
    uv pip install --python /opt/nautobot/.venv/bin/python --system \
    "factory-boy>=3.3.1" \
    "unittest-xml-reporting>=3.2.0" \
    "django-slowtests>=1.1.1" \
    "selenium>=4.0.0" \
    "splinter>=0.21.0"

ENV PATH="/opt/nautobot/.venv/bin:$PATH"
ENV NAUTOBOT_ROOT=/opt/nautobot
ENV NAUTOBOT_CONFIG=/opt/nautobot/nautobot_config.py
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /opt/nautobot

USER 1000:1000
