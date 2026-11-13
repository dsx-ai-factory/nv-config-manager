# syntax=docker/dockerfile:1.7
#
# Project-owned Temporal images.  The upstream images contain a shell and
# debugging utilities; runtime stages below retain only the static binaries and
# data needed by each workload, then run as a non-root distroless user.

# Keep this source line aligned with the currently approved production server
# version.  Changing it requires the Temporal database-upgrade procedure.
ARG TEMPORAL_SERVER_VERSION=1.31.5@sha256:2796f5d67fb5632771afe1fd13ae14d8ad1dddb2e56c8dedc6bf128b85cd448d
# The bootstrap-only admin-tools image supplies Temporal's schema files and
# command-line tools.
ARG TEMPORAL_ADMIN_TOOLS_VERSION=1.31.5@sha256:c48b48626c503660ef2b82811c702d24f17ba6f11a7d8e613ac7bcbfd9111fde
# The UI is independently deployable and does not change Temporal persistence.
ARG TEMPORAL_UI_VERSION=2.54.1@sha256:ff0943fe532b8e33c46cd28b29e81e0ce0b6f55b9ee50a38ef1b437cc4de3fa5

# The version-matched official server and admin-tools binaries use patched Go
# toolchains with the CA distrust mitigation for CVE-2026-111943.
FROM temporalio/server:${TEMPORAL_SERVER_VERSION} AS server-upstream
FROM temporalio/admin-tools:${TEMPORAL_ADMIN_TOOLS_VERSION} AS admin-tools-upstream
FROM temporalio/ui:${TEMPORAL_UI_VERSION} AS ui-upstream

FROM golang:1.27.4-alpine@sha256:0a5f7fe796599ff487605987432e7619ae45beeb2fba4c2ce0b907879932aebf AS dockerize-builder
RUN CGO_ENABLED=0 GOBIN=/out go install github.com/jwilder/dockerize@v0.15.1

FROM golang:1.26.11-alpine@sha256:ae52b5bc6ed045a9ed26ab03dc19327a802a9cc9aa8b27ed57f5c6b60579b0bb AS bootstrap-builder
WORKDIR /src
COPY components/temporal/go.mod ./
COPY components/temporal/cmd/ ./cmd/
RUN CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /out/temporal-bootstrap ./cmd/temporal-bootstrap

# Every runtime COPY of an upstream executable must come through this stage.
# Require a security-patched compiler, allowing later stable Go release series.
FROM bootstrap-builder AS checked-upstream
COPY --from=server-upstream /usr/local/bin/temporal-server /checked/temporal-server
COPY --from=admin-tools-upstream /usr/local/bin/temporal /checked/temporal
COPY --from=admin-tools-upstream /usr/local/bin/temporal-sql-tool /checked/temporal-sql-tool
RUN set -eu; \
    for binary in /checked/*; do \
        actual="$(go version -m "$binary" | head -n 1)"; \
        version="${actual#*: }"; \
        version="${version%% X:*}"; \
        version="${version%%-X:*}"; \
        printf '%s\n' "$version" | grep -Eq '^go1\.[0-9]+\.[0-9]+$' || \
            { echo "$binary: unsupported compiler ($version)" >&2; exit 1; }; \
        release="${version#go1.}"; \
        minor="${release%%.*}"; patch="${release#*.}"; \
        case "$minor" in \
            26) minimum=10 ;; \
            27) minimum=3 ;; \
            *) [ "$minor" -gt 27 ] 2>/dev/null || \
                { echo "$binary: unsupported compiler ($version)" >&2; exit 1; }; minimum=0 ;; \
        esac; \
        [ "$patch" -ge "$minimum" ] 2>/dev/null || \
            { echo "$binary: compiler $version requires Go 1.26.10+ or 1.27.3+" >&2; exit 1; }; \
    done

# Rebuild the version-matched UI server until an upstream release includes the
# patched Go toolchain and dependency versions. The released module contains
# the same embedded frontend assets as the upstream image.
FROM golang:1.26.11-alpine@sha256:ae52b5bc6ed045a9ed26ab03dc19327a802a9cc9aa8b27ed57f5c6b60579b0bb AS ui-server-builder
ARG TEMPORAL_UI_VERSION
WORKDIR /src
RUN ui_version="${TEMPORAL_UI_VERSION%%@*}" && \
    go mod download github.com/temporalio/ui-server/v2@v${ui_version} && \
    cp -R /go/pkg/mod/github.com/temporalio/ui-server/v2@v${ui_version}/. . && \
    chmod -R u+w . && \
    go get golang.org/x/crypto@v0.57.0 \
        golang.org/x/net@v0.60.0 \
        golang.org/x/text@v0.42.0 \
        google.golang.org/grpc@v1.83.2 && \
    CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /out/ui-server ./cmd/server/main.go

# =============================================================================
# Temporal Server
# =============================================================================
FROM nvcr.io/nvidia/distroless/go:v4.1.7@sha256:9ef7b9ac43cbf4c833fd5d98689dc09b06a29e5ec3451796b037aa27928fe532 AS server
COPY --from=checked-upstream /checked/temporal-server /usr/local/bin/temporal-server
COPY --from=dockerize-builder /out/dockerize /usr/local/bin/dockerize
USER nvs
ENTRYPOINT ["/usr/local/bin/temporal-server"]

# =============================================================================
# Temporal Bootstrap
# =============================================================================
# This image carries Temporal's v1.31 schema files plus NVIDIA Config Manager's
# bootstrap binary. It runs only as a chart-managed init container.
FROM nvcr.io/nvidia/distroless/go:v4.1.7@sha256:9ef7b9ac43cbf4c833fd5d98689dc09b06a29e5ec3451796b037aa27928fe532 AS bootstrap
COPY --from=checked-upstream /checked/temporal /usr/local/bin/temporal
COPY --from=checked-upstream /checked/temporal-sql-tool /usr/local/bin/temporal-sql-tool
COPY --from=admin-tools-upstream /etc/temporal/schema /etc/temporal/schema
COPY --from=bootstrap-builder /out/temporal-bootstrap /usr/local/bin/temporal-bootstrap
USER nvs
ENTRYPOINT ["/usr/local/bin/temporal-bootstrap"]

# =============================================================================
# Temporal Web UI
# =============================================================================
FROM nvcr.io/nvidia/distroless/go:v4.1.7@sha256:9ef7b9ac43cbf4c833fd5d98689dc09b06a29e5ec3451796b037aa27928fe532 AS ui
WORKDIR /home/ui-server
COPY --from=ui-upstream /home/ui-server /home/ui-server
COPY --from=ui-server-builder /out/ui-server /home/ui-server/ui-server
USER nvs
ENTRYPOINT ["/home/ui-server/ui-server", "--env", "docker", "start"]
