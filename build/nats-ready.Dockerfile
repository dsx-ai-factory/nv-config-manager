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
ARG GO_VERSION=1.26.9
ARG GO_SHA256_AMD64=42d158b4d8f7b61ac0a830567c940a86098fb7aac52e467a5ebec03ef5cc2f8d
ARG GO_SHA256_ARM64=4a97373d49fcacdcf3694fea368a500b00ee3e963974f3e7514132717632f052

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
FROM nvcr.io/nvidia/distroless/go:v4.1.6@sha256:570f30c4fecbf23ed8312c654a6e50ea1720ede0acca62493455b2ca121c9929

COPY --from=builder /build/bin/nats-ready /nats-ready
USER nvs
ENTRYPOINT ["/nats-ready"]
