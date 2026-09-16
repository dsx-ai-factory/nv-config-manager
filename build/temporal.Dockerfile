# syntax=docker/dockerfile:1.7
#
# Project-owned Temporal images.  The upstream images contain a shell and
# debugging utilities; runtime stages below retain only the static binaries and
# data needed by each workload, then run as a non-root distroless user.

# Keep this source line aligned with the currently approved production server
# version.  Changing it requires the Temporal database-upgrade procedure.
ARG TEMPORAL_SERVER_VERSION=1.29.7
# The bootstrap-only admin-tools image supplies Temporal's schema files and
# command-line tools. Temporal publishes 1.29.7 under this fully qualified tag.
ARG TEMPORAL_ADMIN_TOOLS_VERSION=1.29.7-tctl-1.18.4-cli-1
# The UI is independently deployable and does not change Temporal persistence.
ARG TEMPORAL_UI_VERSION=2.52.1

FROM temporalio/server:${TEMPORAL_SERVER_VERSION} AS server-upstream
FROM temporalio/admin-tools:${TEMPORAL_ADMIN_TOOLS_VERSION} AS admin-tools-upstream
FROM temporalio/ui:${TEMPORAL_UI_VERSION} AS ui-upstream

FROM golang:1.26.5-alpine AS bootstrap-builder
WORKDIR /src
COPY components/temporal/go.mod ./
COPY components/temporal/cmd/ ./cmd/
RUN CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /out/temporal-bootstrap ./cmd/temporal-bootstrap

# Rebuild the version-matched UI server until an upstream release includes the
# patched Go toolchain and dependency versions. The released module contains
# the same embedded frontend assets as the upstream image.
FROM golang:1.26.5-alpine AS ui-server-builder
ARG TEMPORAL_UI_VERSION
WORKDIR /src
RUN go mod download github.com/temporalio/ui-server/v2@v${TEMPORAL_UI_VERSION} && \
    cp -R /go/pkg/mod/github.com/temporalio/ui-server/v2@v${TEMPORAL_UI_VERSION}/. . && \
    chmod -R u+w . && \
    go get golang.org/x/crypto@v0.52.0 \
        golang.org/x/net@v0.55.0 \
        golang.org/x/text@v0.39.0 \
        google.golang.org/grpc@v1.82.1 && \
    CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /out/ui-server ./cmd/server/main.go

# =============================================================================
# Temporal Server
# =============================================================================
FROM nvcr.io/nvidia/distroless/go:v4.1.2@sha256:731531712c92ee24001a4a6e0a0897c4fa542432d7186c5d73b0110ba6d0da16 AS server
COPY --from=server-upstream /usr/local/bin/temporal-server /usr/local/bin/temporal-server
COPY --from=server-upstream /usr/local/bin/dockerize /usr/local/bin/dockerize
USER nvs
ENTRYPOINT ["/usr/local/bin/temporal-server"]

# =============================================================================
# Temporal Bootstrap
# =============================================================================
# This image carries Temporal's v1.29 schema files plus NVIDIA Config Manager's
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
