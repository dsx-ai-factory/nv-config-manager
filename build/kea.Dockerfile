# NVIDIA Config Manager - KEA DHCP Server Image
# Build with: docker build -t nv-config-manager-kea -f Dockerfile.kea .
# This image contains the ISC Kea DHCP4 server with Stork agent for monitoring
#
# NOTE: This image uses Ubuntu base instead of distroless because:
# - ISC Kea packages require apt/dpkg installation
# - Supervisor is needed for process management (kea-dhcp4 + stork-agent)
# - Stork agent requires shell scripts and dynamic configuration

FROM nvcr.io/nvidia/base/ubuntu:noble-20260217@sha256:57a7daab5579d4b4cfbe25b59dc9d22d0c4cec24e5608523e77fd5f12e9da51a

ARG APT_MIRROR=""
ARG APT_MIRROR_GPG_KEY_URL=""

# Install dependencies, Kea DHCP4, and create kea user
COPY --from=scripts configure-apt-mirror.sh /tmp/configure-apt-mirror.sh
RUN set -eux; \
    /tmp/configure-apt-mirror.sh "$APT_MIRROR" "$APT_MIRROR_GPG_KEY_URL" ubuntu && \
    apt-get update && apt-get upgrade -y && \
    apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        gnupg \
        supervisor && \
    # Add ISC Cloudsmith repository for Kea 2.6
    curl -1sLf 'https://dl.cloudsmith.io/public/isc/kea-2-6/setup.deb.sh' | bash && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        isc-kea-ctrl-agent \
        isc-kea-dhcp4 \
        isc-kea-hooks && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    # Create the kea group and user if they don't exist
    { getent group kea >/dev/null || groupadd -r kea; } && \
    { id -u kea >/dev/null 2>&1 || useradd -r -g kea kea; }

# Install Stork agent (pinned to 2.4.x for Go dependency CVE fixes)
COPY build/setup.stork.deb.sh /tmp/setup.stork.deb.sh
RUN bash /tmp/setup.stork.deb.sh && \
    apt-get install -y --no-install-recommends isc-stork-agent=2.4.1* && \
    rm /tmp/setup.stork.deb.sh && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Copy supervisor and kea configuration. Keep this explicit list in sync with build/kea/.
COPY build/kea/etc/kea/kea-ctrl-agent.conf /etc/kea/kea-ctrl-agent.conf
COPY build/kea/etc/kea/kea-dhcp4.conf /etc/kea/kea-dhcp4.conf
COPY build/kea/etc/supervisor/supervisord.conf /etc/supervisor/supervisord.conf
COPY build/kea/etc/supervisor/conf.d/kea-ctrl-agent.conf /etc/supervisor/conf.d/kea-ctrl-agent.conf
COPY build/kea/etc/supervisor/conf.d/kea-dhcp4.conf /etc/supervisor/conf.d/kea-dhcp4.conf
COPY build/kea/etc/supervisor/conf.d/stork-agent.conf /etc/supervisor/conf.d/stork-agent.conf

# Ensure kea and supervisor directories exist with correct permissions
# Update hooks library path for architecture (Ubuntu uses arch-specific paths like
# /usr/lib/aarch64-linux-gnu/kea/hooks or /usr/lib/x86_64-linux-gnu/kea/hooks)
RUN mkdir -p /var/run/kea /var/log/kea /var/lib/kea /var/log/supervisor && \
    chown -R kea:kea /var/run/kea /var/log/kea /var/lib/kea /etc/kea && \
    chmod 644 /etc/kea/kea-dhcp4.conf /etc/kea/kea-ctrl-agent.conf && \
    KEA_HOOKS_PATH=$(ls -d /usr/lib/*/kea/hooks 2>/dev/null | head -1) && \
    sed -i "s|/usr/lib/kea/hooks|${KEA_HOOKS_PATH}|g" /etc/kea/kea-dhcp4.conf

# Expose ports: DHCP (67/UDP), Kea Control Agent (8000/TCP), Stork Agent (9457/TCP)
EXPOSE 67/udp 8000 9457

# Start supervisord which manages kea-dhcp4 and stork-agent
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/supervisord.conf"]
