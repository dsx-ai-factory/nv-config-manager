# syntax=docker/dockerfile:1.7
#
# Project-owned Temporal images.  The upstream images contain a shell and
# debugging utilities; runtime stages below retain only the static binaries and
# data needed by each workload, then run as a non-root distroless user.

# Keep this source line aligned with the currently approved production server
# version.  Changing it requires the Temporal database-upgrade procedure.
ARG TEMPORAL_SERVER_VERSION=1.30.8@sha256:54c59485b185ad787061f9a2ed72bd31c8efeae2d4144c61f22873df52c3c781
# The bootstrap-only admin-tools image supplies Temporal's schema files and
# command-line tools.
ARG TEMPORAL_ADMIN_TOOLS_VERSION=1.30.8@sha256:22d417fd0d541da6b18486912a546b3203f516f0bf9f8b2f27611506fca2507c
# The UI is independently deployable and does not change Temporal persistence.
ARG TEMPORAL_UI_VERSION=2.54.1@sha256:ff0943fe532b8e33c46cd28b29e81e0ce0b6f55b9ee50a38ef1b437cc4de3fa5

# Keep official binaries while awaiting upstream rebuilds for Go's CA distrust
# mitigation: https://go.dev/issue/82345. Refreshed runtime CA stores below are
# separate from that pending compiler/runtime fix.
FROM temporalio/server:${TEMPORAL_SERVER_VERSION} AS server-upstream
FROM temporalio/admin-tools:${TEMPORAL_ADMIN_TOOLS_VERSION} AS admin-tools-upstream
FROM temporalio/ui:${TEMPORAL_UI_VERSION} AS ui-upstream

FROM golang:1.27.3-alpine@sha256:b8c9b82affbbb82a020bbcf998dad2ba9516944ac7e883524642a481a9505e9c AS dockerize-builder
RUN CGO_ENABLED=0 GOBIN=/out go install github.com/jwilder/dockerize@v0.15.1

FROM golang:1.26.10-alpine@sha256:27f2d3b83cfc5803c8078f8e730af0f7c431bf067afaec8f60a36fb763187cba AS bootstrap-builder
WORKDIR /src
COPY components/temporal/go.mod ./
COPY components/temporal/cmd/ ./cmd/
RUN CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /out/temporal-bootstrap ./cmd/temporal-bootstrap

# Rebuild the version-matched UI server until an upstream release includes the
# patched Go toolchain and dependency versions. The released module contains
# the same embedded frontend assets as the upstream image.
FROM golang:1.26.10-alpine@sha256:27f2d3b83cfc5803c8078f8e730af0f7c431bf067afaec8f60a36fb763187cba AS ui-server-builder
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
COPY --from=server-upstream /usr/local/bin/temporal-server /usr/local/bin/temporal-server
COPY --from=dockerize-builder /out/dockerize /usr/local/bin/dockerize
USER nvs
ENTRYPOINT ["/usr/local/bin/temporal-server"]

# =============================================================================
# Temporal Bootstrap
# =============================================================================
# This image carries Temporal's v1.30 schema files plus NVIDIA Config Manager's
# bootstrap binary. It runs only as a chart-managed init container.
FROM nvcr.io/nvidia/distroless/go:v4.1.7@sha256:9ef7b9ac43cbf4c833fd5d98689dc09b06a29e5ec3451796b037aa27928fe532 AS bootstrap
COPY --from=admin-tools-upstream /usr/local/bin/temporal /usr/local/bin/temporal
COPY --from=admin-tools-upstream /usr/local/bin/temporal-sql-tool /usr/local/bin/temporal-sql-tool
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
