# syntax=docker/dockerfile:1.7
# NVIDIA Config Manager - KEA DHCP Server Image
# Build with: docker build -t nv-config-manager-kea -f Dockerfile.kea .
# This image contains the ISC Kea DHCP4 server with Stork agent for monitoring
#
# NOTE: This image uses Ubuntu base instead of distroless because:
# - ISC Kea packages require apt/dpkg installation
# - Supervisor is needed for process management (kea-dhcp4 + stork-agent)
# - Stork agent requires shell scripts and dynamic configuration

# Keep Stork 2.4.2 behavior while replacing the package's Go 1.26.8 and x/net
# 0.59.0, which are affected by the October Go security advisories.
# Remove this rebuild when the stable ISC package carries patched dependencies.
# Use glibc and CGO, matching the packaged agent. Bookworm's glibc remains
# compatible with the newer Ubuntu runtime below.
FROM golang:1.26.11-bookworm@sha256:dc40a13b61304647ed7e5d581d246b86413dfc29d63c6d018310feb93cd98fc0 AS stork-builder
ADD --checksum=sha256:ab3f40c6613ebcc62afaa2d0843df0e888d52d1c2c91ef50f6d593dd903a3238 \
    https://github.com/isc-projects/stork/archive/977ad7d882ff88599feeee6c283951e315ce8923.tar.gz /tmp/stork.tar.gz
RUN mkdir /src && tar -xzf /tmp/stork.tar.gz -C /src --strip-components=1
WORKDIR /src/backend
RUN apt-get update && apt-get install --no-install-recommends -y protobuf-compiler && \
    go install google.golang.org/protobuf/cmd/protoc-gen-go@v1.36.11 && \
    go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@v1.6.2 && \
    (cd api && protoc --proto_path=. --go_out=. --go-grpc_out=. agent.proto) && \
    go get golang.org/x/net@v0.62.0 && \
    go build -o /tmp/stork-code-gen ./cmd/stork-code-gen && \
    for family in 4 6; do \
        /tmp/stork-code-gen std-option-defs \
            --input "../codegen/std_dhcpv${family}_option_def.json" \
            --output "daemoncfg/kea/stdoptiondef${family}.go" \
            --template "daemoncfg/kea/stdoptiondef${family}.go.template" || exit 1; \
    done && \
    CGO_ENABLED=1 go build -trimpath -ldflags='-s -w' -o /out/stork-agent ./cmd/stork-agent

FROM nvcr.io/nvidia/base/ubuntu:noble-20260917@sha256:7e0f726d635d440d28a7f18e6498d3bdb76c7038bd4c90a090c21eb7c9f40686

ARG APT_MIRROR=""
ARG APT_MIRROR_GPG_KEY_URL=""

# Install dependencies, Kea DHCP4, and create kea user
COPY --from=scripts configure-apt-mirror.sh /tmp/configure-apt-mirror.sh
COPY build/setup.kea.deb.sh /tmp/setup.kea.deb.sh
RUN set -eux; \
    /tmp/configure-apt-mirror.sh "$APT_MIRROR" "$APT_MIRROR_GPG_KEY_URL" ubuntu && \
    apt-get update && apt-get upgrade -y && \
    apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        gnupg \
        supervisor && \
    { dpkg --compare-versions "$(dpkg-query -W -f='${Version}' ca-certificates)" ge 20260816ubuntu0.24.04.1 || \
        { echo "ca-certificates < 20260816ubuntu0.24.04.1" >&2; exit 1; }; } && \
    # Add ISC Cloudsmith repository for Kea 2.6
    bash /tmp/setup.kea.deb.sh && \
    rm /tmp/setup.kea.deb.sh && \
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

# Retain the package's configuration and supporting files; use the patched,
# version-matched executable from the builder stage.
COPY build/setup.stork.deb.sh /tmp/setup.stork.deb.sh
RUN bash /tmp/setup.stork.deb.sh && \
    apt-get install -y --no-install-recommends isc-stork-agent=2.4.2* && \
    rm /tmp/setup.stork.deb.sh && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*
COPY --from=stork-builder /out/stork-agent /usr/bin/stork-agent

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
