# syntax=docker/dockerfile:1.7
#
# Project-owned Temporal images.  The upstream images contain a shell and
# debugging utilities; runtime stages below retain only the static binaries and
# data needed by each workload, then run as a non-root distroless user.

# Keep this source line aligned with the currently approved production server
# version.  Changing it requires the Temporal database-upgrade procedure.
ARG TEMPORAL_SERVER_VERSION=1.30.7@sha256:dc8dc6fffa29de3bad7f5c39cb406db3104053fbb0c2494e7c76016211bc99b0
# The bootstrap-only admin-tools image supplies Temporal's schema files and
# command-line tools.
ARG TEMPORAL_ADMIN_TOOLS_VERSION=1.30.7@sha256:bbdf5fe91b288c17442efbbfb42daa3668d09cfa8ebeb078d6fd475a97f66b81
# The UI is independently deployable and does not change Temporal persistence.
ARG TEMPORAL_UI_VERSION=2.54.1@sha256:ff0943fe532b8e33c46cd28b29e81e0ce0b6f55b9ee50a38ef1b437cc4de3fa5

FROM temporalio/server:${TEMPORAL_SERVER_VERSION} AS server-upstream
FROM temporalio/admin-tools:${TEMPORAL_ADMIN_TOOLS_VERSION} AS admin-tools-upstream
FROM temporalio/ui:${TEMPORAL_UI_VERSION} AS ui-upstream

FROM golang:1.27-alpine@sha256:8a5910f31396cd4d89662f56c68b3ae31d374308270a1c3bd96672ee5ed43414 AS dockerize-builder
RUN CGO_ENABLED=0 GOBIN=/out go install github.com/jwilder/dockerize@v0.15.1

FROM golang:1.26.8-alpine@sha256:8ac98ca534ac3f51e1f420a1dd2c15e74c75cfa0f23f3ad27eb5d7236c349a0c AS bootstrap-builder
WORKDIR /src
COPY components/temporal/go.mod ./
COPY components/temporal/cmd/ ./cmd/
RUN CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /out/temporal-bootstrap ./cmd/temporal-bootstrap

# Rebuild the version-matched UI server until an upstream release includes the
# patched Go toolchain and dependency versions. The released module contains
# the same embedded frontend assets as the upstream image.
FROM golang:1.26.6-alpine@sha256:3889b425f035be855a72fb4755265311293b6d414521f0a519d819df32222d83 AS ui-server-builder
ARG TEMPORAL_UI_VERSION
WORKDIR /src
RUN ui_version="${TEMPORAL_UI_VERSION%%@*}" && \
    go mod download github.com/temporalio/ui-server/v2@v${ui_version} && \
    cp -R /go/pkg/mod/github.com/temporalio/ui-server/v2@v${ui_version}/. . && \
    chmod -R u+w . && \
    go get golang.org/x/crypto@v0.55.0 \
        golang.org/x/net@v0.57.0 \
        golang.org/x/text@v0.41.0 \
        google.golang.org/grpc@v1.82.1 && \
    CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /out/ui-server ./cmd/server/main.go

# =============================================================================
# Temporal Server
# =============================================================================
FROM nvcr.io/nvidia/distroless/go:v4.1.2@sha256:731531712c92ee24001a4a6e0a0897c4fa542432d7186c5d73b0110ba6d0da16 AS server
COPY --from=server-upstream /usr/local/bin/temporal-server /usr/local/bin/temporal-server
COPY --from=dockerize-builder /out/dockerize /usr/local/bin/dockerize
USER nvs
ENTRYPOINT ["/usr/local/bin/temporal-server"]

# =============================================================================
# Temporal Bootstrap
# =============================================================================
# This image carries Temporal's v1.30 schema files plus NVIDIA Config Manager's
# bootstrap binary. It runs only as a chart-managed init container.
FROM nvcr.io/nvidia/distroless/go:v4.1.2@sha256:731531712c92ee24001a4a6e0a0897c4fa542432d7186c5d73b0110ba6d0da16 AS bootstrap
COPY --from=admin-tools-upstream /usr/local/bin/temporal /usr/local/bin/temporal
COPY --from=admin-tools-upstream /usr/local/bin/temporal-sql-tool /usr/local/bin/temporal-sql-tool
COPY --from=admin-tools-upstream /etc/temporal/schema /etc/temporal/schema
COPY --from=bootstrap-builder /out/temporal-bootstrap /usr/local/bin/temporal-bootstrap
USER nvs
ENTRYPOINT ["/usr/local/bin/temporal-bootstrap"]

# =============================================================================
# Temporal Web UI
# =============================================================================
FROM nvcr.io/nvidia/distroless/go:v4.1.2@sha256:731531712c92ee24001a4a6e0a0897c4fa542432d7186c5d73b0110ba6d0da16 AS ui
WORKDIR /home/ui-server
COPY --from=ui-upstream /home/ui-server /home/ui-server
COPY --from=ui-server-builder /out/ui-server /home/ui-server/ui-server
USER nvs
ENTRYPOINT ["/home/ui-server/ui-server", "--env", "docker", "start"]
