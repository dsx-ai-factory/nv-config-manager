# NVIDIA Config Manager - NATS Ready Check
#
# Uses NVIDIA distroless Go image for minimal attack surface.
# Multi-stage build: builder compiles the binary on Ubuntu, runtime uses distroless.

# =============================================================================
# Builder stage - compile the Go binary
# =============================================================================
FROM nvcr.io/nvidia/base/ubuntu:noble-20260217@sha256:57a7daab5579d4b4cfbe25b59dc9d22d0c4cec24e5608523e77fd5f12e9da51a AS builder

ARG APT_MIRROR=""
ARG APT_MIRROR_GPG_KEY_URL=""

# Official SHA256 checksums from https://go.dev/dl/
ARG GO_VERSION=1.26.6
ARG GO_SHA256_AMD64=708effb774be8237570d0add163225abbdfaf4fca28b2611df167beba4feef89
ARG GO_SHA256_ARM64=d0507e9e9d7fe012aae570108cbd76c15de879e17130ab8cb90d4d7445cb1f2e

# Install Go with checksum verification
COPY --from=scripts configure-apt-mirror.sh /tmp/configure-apt-mirror.sh
RUN set -eux; \
    /tmp/configure-apt-mirror.sh "$APT_MIRROR" "$APT_MIRROR_GPG_KEY_URL" ubuntu && \
    apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl && \
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
FROM nvcr.io/nvidia/distroless/go:v4.1.2@sha256:731531712c92ee24001a4a6e0a0897c4fa542432d7186c5d73b0110ba6d0da16

COPY --from=builder /build/bin/nats-ready /nats-ready
USER nvs
ENTRYPOINT ["/nats-ready"]
