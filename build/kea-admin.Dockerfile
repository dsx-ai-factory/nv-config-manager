# NVIDIA Config Manager - Kea Database Admin Image
# Build with: docker build -t nv-config-manager-kea-admin -f Dockerfile.kea-admin .
#
# This image uses the official ISC kea-admin tool for database initialization
# and upgrades. Using the official tool avoids licensing concerns and ensures
# compatibility with the Kea version in use.
#
# Reference: https://kea.readthedocs.io/en/kea-2.6.2/arm/admin.html
#
# NOTE: This image uses Ubuntu base instead of distroless because:
# - ISC kea-admin package requires apt/dpkg installation
# - Entrypoint shell script requires bash

FROM nvcr.io/nvidia/base/ubuntu:noble-20260917@sha256:7e0f726d635d440d28a7f18e6498d3bdb76c7038bd4c90a090c21eb7c9f40686

ARG APT_MIRROR=""
ARG APT_MIRROR_GPG_KEY_URL=""

# Install kea-admin from ISC Cloudsmith repository
# The isc-kea-admin package contains the database administration tools
COPY --from=scripts configure-apt-mirror.sh /tmp/configure-apt-mirror.sh
RUN set -eux; \
    /tmp/configure-apt-mirror.sh "$APT_MIRROR" "$APT_MIRROR_GPG_KEY_URL" ubuntu && \
    apt-get update && apt-get upgrade -y && \
    apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        gnupg \
        postgresql-client && \
    dpkg --compare-versions "$(dpkg-query -W -f='${Version}' ca-certificates)" ge 20260816ubuntu0.24.04.1 && \
    # Add ISC Cloudsmith repository for Kea 2.6
    curl -1sLf 'https://dl.cloudsmith.io/public/isc/kea-2-6/setup.deb.sh' | bash && \
    # Install only the admin tools (not the full DHCP server)
    apt-get update && \
    apt-get install -y --no-install-recommends isc-kea-admin && \
    # Cleanup
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Copy the entrypoint script
COPY build/kea-admin/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Database initialization only requires config-file reads and network access.
USER 1000:1000

ENTRYPOINT ["/entrypoint.sh"]
