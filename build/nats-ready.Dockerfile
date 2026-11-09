# NVIDIA Config Manager - NATS Ready Check
#
# Uses NVIDIA distroless Go image for minimal attack surface.
# Multi-stage build: builder compiles the binary on Ubuntu, runtime uses distroless.

# =============================================================================
# Builder stage - compile the Go binary
# =============================================================================
FROM nvcr.io/nvidia/base/ubuntu:noble-20260917@sha256:7e0f726d635d440d28a7f18e6498d3bdb76c7038bd4c90a090c21eb7c9f40686 AS builder

ARG APT_MIRROR=""
ARG APT_MIRROR_GPG_KEY_URL=""

# Official SHA256 checksums from https://go.dev/dl/
ARG GO_VERSION=1.26.10
ARG GO_SHA256_AMD64=ccfe7ae822d2c2f41f4fe7c956199967a4dac22d0ee0d2b2bfdb440aab3b20c5
ARG GO_SHA256_ARM64=3f4227ce7b206a513e794f1393adb473a07b46e94d18bd15d1c684bbb7319797

# Install Go with checksum verification
COPY --from=scripts configure-apt-mirror.sh /tmp/configure-apt-mirror.sh
RUN set -eux; \
    /tmp/configure-apt-mirror.sh "$APT_MIRROR" "$APT_MIRROR_GPG_KEY_URL" ubuntu && \
    apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl && \
    { dpkg --compare-versions "$(dpkg-query -W -f='${Version}' ca-certificates)" ge 20260816ubuntu0.24.04.1 || \
        { echo "ca-certificates < 20260816ubuntu0.24.04.1" >&2; exit 1; }; } && \
    ARCH=$(dpkg --print-architecture) && \
    TARBALL="go${GO_VERSION}.linux-${ARCH}.tar.gz" && \
    if [ "$ARCH" = "amd64" ]; then EXPECTED="$GO_SHA256_AMD64"; \
    elif [ "$ARCH" = "arm64" ]; then EXPECTED="$GO_SHA256_ARM64"; \
    else echo "Unsupported architecture: $ARCH" >&2; exit 1; fi && \
    curl -fsSL -o /tmp/${TARBALL} "https://go.dev/dl/${TARBALL}" && \
    echo "${EXPECTED}  /tmp/${TARBALL}" | sha256sum -c - && \
    tar -C /usr/local -xzf /tmp/${TARBALL} && \
    rm /tmp/${TARBALL} && \
    rm -rf /var/lib/apt/lists/*

ENV PATH="/usr/local/go/bin:$PATH"
ENV CGO_ENABLED=0

# Note: Build context is components/nats-ready/
WORKDIR /build
COPY go.mod go.sum ./
COPY cmd/nats-ready/ ./cmd/nats-ready/
COPY internal/nats-ready/ ./internal/nats-ready/

# Build static binary
RUN go build -ldflags="-s -w" -o bin/nats-ready ./cmd/nats-ready

# =============================================================================
# Runtime stage - NVIDIA distroless Go image (minimal, no shell)
# =============================================================================
FROM nvcr.io/nvidia/distroless/go:v4.1.7@sha256:9ef7b9ac43cbf4c833fd5d98689dc09b06a29e5ec3451796b037aa27928fe532

COPY --from=builder /build/bin/nats-ready /nats-ready
USER nvs
ENTRYPOINT ["/nats-ready"]
