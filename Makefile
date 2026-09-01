.PHONY: help install dev test lint format sort-check sort-fix clean docker-build docker-push ui-install ui-dev ui-build \
        local-up local-down local-destroy local-status local-logs deploy kind-up kind-up-sec kind-up-sec-kgateway kind-up-secure kind-down topology install-cert workflow-perf-seed \
        openapi openapi-check go-bindings api-generate docs-assets docs-assets-check docs-format docs-lint docs-lint-fern docs-live docs-preview docs-publish docs-publish-in-ci docs-screenshots docs-air-sim-screenshots docs-ui-screenshots \
        obs-grafana obs-prometheus obs-loki obs-alloy obs-port-forward obs-port-forward-stop

# Configuration
NAMESPACE ?= nv-config-manager
RELEASE_NAME ?= nv-config-manager
HOSTNAME ?= config-manager.local
KIND_CLUSTER_NAME ?= nv-config-manager
# Kind cluster config. CI overrides this with a copy patched to route in-cluster
# docker.io pulls through a Docker Hub mirror (see
# scripts/configure-kind-dockerhub-mirror), which keeps the security stack's
# ~2x-heavier docker.io pulls off the shared runner's anonymous rate limit.
KIND_CONFIG ?= deploy/kind-config.yaml
DEPLOY_SIZE ?= small  # Resource sizing: small (24GB Mac) or medium (64GB VM)
INSTALL_CONFIG ?= deploy/configs/local-superpod.yaml
KIND_SEC_INSTALL_CONFIG ?= deploy/configs/local-sec.yaml
KIND_SEC_NAMESPACE ?= nv-config-manager
KIND_SEC_HOSTNAME ?= config-manager.local
KIND_SEC_KEYCLOAK_HOSTNAME ?= keycloak.$(KIND_SEC_HOSTNAME)
KIND_SEC_SPIFFE_TRUST_DOMAIN ?= $(KIND_SEC_HOSTNAME)
KIND_SEC_FULLNAME ?= $(if $(findstring nv-config-manager,$(RELEASE_NAME)),$(RELEASE_NAME),$(RELEASE_NAME)-nv-config-manager)
KIND_SEC_GATEWAY_CA_SECRET ?= $(KIND_SEC_FULLNAME)-gateway-ca
KIND_SEC_OIDC_CLIENT_SECRET ?= nvcm-local-client-secret
KIND_SEC_KEYCLOAK_ADMIN_PASSWORD ?= admin
KIND_SEC_RENDERED_CONFIG ?= /tmp/nvcm-local-sec-$(KIND_CLUSTER_NAME).yaml
KIND_SEC_GATEWAY_CONTROLLER ?= envoyGateway
KIND_SEC_GATEWAY_CONTROLLERS := envoyGateway kgateway
ifneq ($(filter $(KIND_SEC_GATEWAY_CONTROLLER),$(KIND_SEC_GATEWAY_CONTROLLERS)),$(KIND_SEC_GATEWAY_CONTROLLER))
$(error KIND_SEC_GATEWAY_CONTROLLER must be one of $(KIND_SEC_GATEWAY_CONTROLLERS), got "$(KIND_SEC_GATEWAY_CONTROLLER)")
endif
WORKFLOW_PERF_COUNT ?= 100
WORKFLOW_PERF_RUNNING_COUNT ?= 150
WORKFLOW_PERF_FAILED_COUNT ?= 1
# Pinned like the Ruff version in pyproject.toml. Bump deliberately.
KEEP_SORTED_VERSION ?= v0.10.0

# Generate unique image tag: SHA-TIMESTAMP (e.g., abc1234-1704067200)
GIT_SHA := $(shell git rev-parse --short HEAD 2>/dev/null || echo "unknown")
TIMESTAMP := $(shell date +%s)
LOCAL_TAG ?= $(GIT_SHA)-$(TIMESTAMP)

APT_MIRROR ?=
APT_MIRROR_DEBIAN ?=
APT_MIRROR_GPG_KEY_URL ?=
APT_MIRROR_ARGS = --build-context scripts=build/ $(if $(APT_MIRROR),--build-arg APT_MIRROR=$(APT_MIRROR),) $(if $(APT_MIRROR_GPG_KEY_URL),--build-arg APT_MIRROR_GPG_KEY_URL=$(APT_MIRROR_GPG_KEY_URL),)
APT_MIRROR_DEBIAN_ARGS = --build-context scripts=build/ $(if $(APT_MIRROR_DEBIAN),--build-arg APT_MIRROR_DEBIAN=$(APT_MIRROR_DEBIAN),) $(if $(APT_MIRROR_GPG_KEY_URL),--build-arg APT_MIRROR_GPG_KEY_URL=$(APT_MIRROR_GPG_KEY_URL),)
LATEST_RELEASE_TAG = $(shell git tag --list 2>/dev/null | grep -E '^[v]?[0-9]+\.[0-9]+\.[0-9]+(-rc\.?[0-9]+)?$$' | sort -V 2>/dev/null | tail -n 1)
TEMPLATE_ENGINE_BASE_VERSION = $(subst -rc,rc,$(subst -rc.,rc,$(patsubst v%,%,$(LATEST_RELEASE_TAG))))
TEMPLATE_ENGINE_VERSION ?= $(if $(TEMPLATE_ENGINE_BASE_VERSION),$(TEMPLATE_ENGINE_BASE_VERSION)+g$(GIT_SHA),)
TEMPLATE_ENGINE_VERSION_ARG = $(if $(TEMPLATE_ENGINE_VERSION),--build-arg TEMPLATE_ENGINE_VERSION=$(TEMPLATE_ENGINE_VERSION),)
NVCM_NUMPY_FROM_SOURCE ?=
NVCM_NUMPY_CPU_BASELINE ?=
NVCM_NUMPY_CPU_DISPATCH ?=
NVCM_NUMPY_ALLOW_NOBLAS ?=
NVCM_NUMPY_BUILD_ARGS = $(if $(NVCM_NUMPY_FROM_SOURCE),--build-arg NVCM_NUMPY_FROM_SOURCE=$(NVCM_NUMPY_FROM_SOURCE),) $(if $(NVCM_NUMPY_CPU_BASELINE),--build-arg NVCM_NUMPY_CPU_BASELINE=$(NVCM_NUMPY_CPU_BASELINE),) $(if $(NVCM_NUMPY_CPU_DISPATCH),--build-arg NVCM_NUMPY_CPU_DISPATCH=$(NVCM_NUMPY_CPU_DISPATCH),) $(if $(NVCM_NUMPY_ALLOW_NOBLAS),--build-arg NVCM_NUMPY_ALLOW_NOBLAS=$(NVCM_NUMPY_ALLOW_NOBLAS),)
NAUTOBOT_APP_OVERLAYS_VERSION ?= $(TEMPLATE_ENGINE_VERSION)
NAUTOBOT_APP_OVERLAYS_VERSION_ARG = $(if $(NAUTOBOT_APP_OVERLAYS_VERSION),--build-arg NAUTOBOT_APP_OVERLAYS_VERSION=$(NAUTOBOT_APP_OVERLAYS_VERSION),)
NAUTOBOT_NV_CONFIG_MANAGER_VERSION ?= $(TEMPLATE_ENGINE_VERSION)
NAUTOBOT_NV_CONFIG_MANAGER_VERSION_ARG = $(if $(NAUTOBOT_NV_CONFIG_MANAGER_VERSION),--build-arg NAUTOBOT_NV_CONFIG_MANAGER_VERSION=$(NAUTOBOT_NV_CONFIG_MANAGER_VERSION),)
# Keep this aligned with the currently approved production server version.
# A Temporal server upgrade is a separately planned schema migration.
TEMPORAL_SERVER_VERSION ?= 1.29.7
# Admin tools run only in the bootstrap init containers. Temporal publishes
# 1.29.7 under its fully qualified server/tctl/CLI tag.
TEMPORAL_ADMIN_TOOLS_VERSION ?= 1.29.7-tctl-1.18.4-cli-1
# UI is independently deployable and does not change Temporal persistence.
TEMPORAL_UI_VERSION ?= 2.52.1
TEMPORAL_BUILD_ARGS = --build-arg TEMPORAL_SERVER_VERSION=$(TEMPORAL_SERVER_VERSION) --build-arg TEMPORAL_ADMIN_TOOLS_VERSION=$(TEMPORAL_ADMIN_TOOLS_VERSION) --build-arg TEMPORAL_UI_VERSION=$(TEMPORAL_UI_VERSION)

# Default target
help:
	@echo "NVIDIA Config Manager - Development Commands"
	@echo ""
	@echo "Local Kubernetes Deployment (via nv-config-manager-installer):"
	@echo "  make local-up         - Build images and deploy to local k8s"
	@echo "  make local-down       - Remove local deployment (preserves operators)"
	@echo "  make local-destroy    - Complete cleanup including shared operators (with prompt)"
	@echo "  make local-status     - Show status of local deployment"
	@echo "  make local-logs       - Tail logs from all services"
	@echo "  make local-restart    - Restart all deployments"
	@echo ""
	@echo "Kind Cluster Management:"
	@echo "  make kind-up                      - Create Kind cluster and deploy NVIDIA Config Manager (small sizing, 24GB)"
	@echo "  make kind-up-sec                  - Create Kind cluster with local Keycloak, SPIRE, and workflow RBAC"
	@echo "  make kind-up-sec-kgateway         - Same secured Kind deployment using kgateway"
	@echo "  make kind-up DEPLOY_SIZE=medium   - Deploy with medium sizing (64GB VM)"
	@echo "  make kind-down                    - Delete Kind cluster"
	@echo "  make topology                     - Populate Nautobot with mock topology data"
	@echo "  make workflow-perf-seed           - Seed local Temporal with pending/running/failing workflows for list latency testing"
	@echo "  make install-cert                 - Trust the local gateway CA in system, browser, and Node.js tools"
	@echo ""
	@echo "Docker Build:"
	@echo "  make docker-build     - Build all Docker images locally"
	@echo "  make docker-build-nb  - Build Nautobot image only"
	@echo "  make docker-push      - Push Docker images to registry"
	@echo ""
	@echo "Multi-Arch Builds (amd64 + arm64):"
	@echo "  make docker-buildx-setup          - Setup Docker Buildx for multi-arch (with QEMU)"
	@echo "  make docker-build-multiarch       - Build & push all images (multi-arch)"
	@echo "  make docker-build-nv-config-manager-multiarch  - Build & push nv-config-manager image (multi-arch)"
	@echo "  make docker-build-ui-multiarch    - Build & push nv-config-manager-ui image (multi-arch)"
	@echo "  make docker-build-nb-multiarch    - Build & push nv-config-manager-nautobot image (multi-arch)"
	@echo "  make docker-build-nats-ready-multiarch - Build & push nats-ready (multi-arch)"
	@echo ""
	@echo "Single-Arch Builds (for CI parallel builds):"
	@echo "  make docker-buildx-setup-native   - Setup buildx for native builds"
	@echo "  make docker-buildx-setup          - Setup buildx with QEMU for cross-arch"
	@echo "  make docker-build-all             - Build & push all images for PLATFORM"
	@echo "  make docker-manifest-create       - Create multi-arch manifests from arch images"
	@echo "  Example (amd64):   make docker-buildx-setup-native && make docker-build-all PLATFORM=linux/amd64"
	@echo "  Example (arm64):   make docker-buildx-setup-native && make docker-build-all PLATFORM=linux/arm64"
	@echo ""
	@echo "Python Commands:"
	@echo "  make install          - Install Python dependencies"
	@echo "  make dev              - Install with dev dependencies"
	@echo "  make test             - Run all tests (parallel)"
	@echo "  make test-cov         - Run tests with coverage (parallel)"
	@echo "  make test-integration - Run integration tests (requires running cluster)"
	@echo "  make test-nautobot-plugin - Run vendored Nautobot plugin's Django suite in one-shot container"
	@echo "  make lint             - Run linters"
	@echo "  make format           - Format code"
	@echo "  make clean            - Clean build artifacts"
	@echo ""
	@echo "UI Commands:"
	@echo "  make ui-install       - Install UI dependencies"
	@echo "  make ui-dev           - Run UI development server"
	@echo "  make ui-build         - Build UI for production"
	@echo ""
	@echo "Documentation:"
	@echo "  make openapi          - Generate OpenAPI specs for all FastAPI services"
	@echo "  make openapi-check    - Check if OpenAPI specs are up-to-date"
	@echo "  make go-bindings      - Generate Go clients from the committed OpenAPI specs"
	@echo "  make api-generate     - Regenerate OpenAPI specs and Go clients"
	@echo "  make docs-assets      - Mirror source assets into Fern docs assets"
	@echo "  make docs-assets-check - Check if mirrored docs assets are up-to-date"
	@echo "  make docs-lint        - Lint documentation markdown with rumdl"
	@echo "  make docs-lint-fern   - Validate Fern docs configuration and markdown"
	@echo "  make docs-live        - Start the Fern docs dev server"
	@echo "  make docs-preview     - Generate a Fern docs preview"
	@echo "  make docs-publish     - Publish the Fern docs"
	@echo "  make docs-screenshots - Regenerate installer TUI screenshots for docs"
	@echo "  make docs-air-sim-screenshots - Regenerate DSX Air sim TUI screenshots for docs"
	@echo "  make docs-ui-screenshots - Regenerate Next.js workflow screenshots for docs"
	@echo ""
	@echo "Observability (local-dev stack only — requires observability to be enabled in installer config):"
	@echo "  make obs-grafana             - Port-forward Grafana       -> http://localhost:3000  (admin/admin)"
	@echo "  make obs-prometheus          - Port-forward Prometheus    -> http://localhost:9090"
	@echo "  make obs-loki                - Port-forward Loki          -> http://localhost:3100"
	@echo "  make obs-alloy               - Port-forward Alloy UI      -> http://localhost:12345"
	@echo "  make obs-port-forward        - Run all four in background"
	@echo "  make obs-port-forward-stop   - Kill all observability port-forwards"
	@echo ""
	@echo "Service Commands (standalone, no k8s):"
	@echo "  make run-ztp-api          - Run ZTP API"
	@echo "  make run-dhcp-api         - Run DHCP API"
	@echo "  make run-temporal-api     - Run Temporal API"
	@echo "  make run-temporal-worker  - Run Temporal Worker"
	@echo "  make run-render-api       - Run Render API"
	@echo "  make run-config-store-api - Run Config Store API"

# Python targets
install:
	uv sync --no-dev

dev:
	uv sync

test:
	uv run pytest -n auto

test-cov:
	uv run pytest -n auto --cov=src/nv_config_manager --cov-report=html --cov-report=xml

test-integration:
	@echo "🧪 Running integration tests against running cluster..."
	@echo "   Requires: NVIDIA Config Manager deployed to local k8s (run 'make kind-up' first)"
	uv run pytest src/tests/integration/ -v --timeout=900

test-integration-local:
	@echo "🧪 Running integration tests against local Envoy Gateway..."
	@echo "   Using namespace: $(NAMESPACE)"
	uv run pytest src/tests/integration/ -v --nv-config-manager-namespace $(NAMESPACE) --timeout=900

# Runs the vendored Nautobot plugin's Django test suite (nv_config_manager/tests/)
# inside a one-shot container built from the local nautobot image. Spins up
# throwaway postgres + redis on a private docker network, runs `nautobot-server
# test`, then tears everything down. Override TEST_LABEL or TEST_ARGS to scope.
#
# Examples:
#   make test-nautobot-plugin
#   make test-nautobot-plugin TEST_LABEL=nv_config_manager.tests.test_models
#   make test-nautobot-plugin TEST_ARGS="--buffer --verbosity 2"
#   LOCAL_TAG=abc1234-1234567890 make test-nautobot-plugin   # reuse existing image
TEST_LABEL ?= nv_config_manager
TEST_ARGS ?= --buffer --failfast
NB_IMAGE := nv-config-manager-nautobot:$(LOCAL_TAG)
NB_TEST_IMAGE := nv-config-manager-nautobot-test:$(LOCAL_TAG)

test-nautobot-plugin:
	@if ! docker image inspect $(NB_IMAGE) >/dev/null 2>&1; then \
		echo "🏗️  Base image $(NB_IMAGE) not found, building..."; \
		$(MAKE) docker-build-nb; \
	else \
		echo "📦 Reusing existing base image $(NB_IMAGE)"; \
	fi
	@if ! docker image inspect $(NB_TEST_IMAGE) >/dev/null 2>&1; then \
		echo "🏗️  Building test image $(NB_TEST_IMAGE) (adds factory-boy + friends)..."; \
		docker build --provenance=false \
			--build-arg BASE_TAG=$(LOCAL_TAG) \
			-t $(NB_TEST_IMAGE) \
			-f build/nautobot-test.Dockerfile build/; \
	else \
		echo "📦 Reusing existing test image $(NB_TEST_IMAGE)"; \
	fi
	@echo "🧪 Running plugin Django tests: $(TEST_LABEL) [$(TEST_ARGS)]"
	@set -e; \
	SUFFIX=$$$$; \
	NET=nv-cm-nbtest-$$SUFFIX; \
	PG=nv-cm-nbtest-pg-$$SUFFIX; \
	RD=nv-cm-nbtest-rd-$$SUFFIX; \
	cleanup() { \
		docker rm -f $$PG $$RD >/dev/null 2>&1 || true; \
		docker network rm $$NET >/dev/null 2>&1 || true; \
	}; \
	trap cleanup EXIT INT TERM; \
	docker network create $$NET >/dev/null; \
	docker run -d --rm --name $$PG --network $$NET \
		-e POSTGRES_DB=nautobot \
		-e POSTGRES_USER=nautobot \
		-e POSTGRES_PASSWORD=nautobot \
		postgres:16-alpine >/dev/null; \
	docker run -d --rm --name $$RD --network $$NET \
		redis:7-alpine redis-server --requirepass changeme >/dev/null; \
	echo "⏳ Waiting for postgres..."; \
	for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do \
		if docker exec $$PG pg_isready -U nautobot >/dev/null 2>&1; then break; fi; \
		sleep 1; \
	done; \
	docker run --rm --network $$NET \
		-v $(CURDIR)/components/nautobot/nautobot_test_config.py:/opt/nautobot/nautobot_test_config.py:ro \
		-e NAUTOBOT_CONFIG=/opt/nautobot/nautobot_test_config.py \
		-e NAUTOBOT_SECRET_KEY=plugin-test-suite-secret-not-for-prod \
		-e NAUTOBOT_DB_ENGINE=django.db.backends.postgresql \
		-e NAUTOBOT_DB_HOST=$$PG \
		-e NAUTOBOT_DB_NAME=nautobot \
		-e NAUTOBOT_DB_USER=nautobot \
		-e NAUTOBOT_DB_PASSWORD=nautobot \
		-e NAUTOBOT_REDIS_HOST=$$RD \
		-e NAUTOBOT_REDIS_PORT=6379 \
		-e NAUTOBOT_REDIS_PASSWORD=changeme \
		-e NAUTOBOT_JOBS_ROOT=/tmp/nb-test/jobs \
		-e NAUTOBOT_GIT_ROOT=/tmp/nb-test/git \
		-e NAUTOBOT_MEDIA_ROOT=/tmp/nb-test/media \
		-e NAUTOBOT_STATIC_ROOT=/tmp/nb-test/static \
		--entrypoint /opt/nautobot/.venv/bin/nautobot-server \
		$(NB_TEST_IMAGE) \
		test $(TEST_LABEL) $(TEST_ARGS)

# Builds just the test image (assumes base nb image is already built).
docker-build-nb-test:
	@echo "🏗️  Building Nautobot test image (adds plugin test deps)..."
	docker build --provenance=false \
		--build-arg BASE_TAG=$(LOCAL_TAG) \
		-t $(NB_TEST_IMAGE) \
		-f build/nautobot-test.Dockerfile build/
	@echo "✅ Built $(NB_TEST_IMAGE)"

lint: sort-check
	uv run ruff check src/ packages/
	uv run ty check src/nv_config_manager/ packages/
	uv run mypy src/nv_config_manager packages --no-incremental

format: sort-fix
	uv run ruff format src/ packages/
	uv run ruff check --fix src/ packages/

# Enforces alphabetical order for lists marked with `# keep-sorted start` /
# `# keep-sorted end` comments (see src/nv_config_manager/temporal/ngc/workflows/__init__.py).
sort-check:
	find src -name '*.py' -print0 | xargs -0 go run github.com/google/keep-sorted@$(KEEP_SORTED_VERSION) --mode=lint

sort-fix:
	find src -name '*.py' -print0 | xargs -0 go run github.com/google/keep-sorted@$(KEEP_SORTED_VERSION) --mode=fix

# OpenAPI spec generation
openapi:
	uv run python scripts/generate_openapi.py

openapi-check:
	uv run python scripts/generate_openapi.py --check

go-bindings:
	./scripts/generate_go_bindings.sh

api-generate:
	$(MAKE) openapi
	$(MAKE) go-bindings

# Documentation targets
docs-assets:
	cp -p deploy/helm/dashboards/nv-config-manager-overview.json docs/assets/static/nv-config-manager-overview.json

docs-assets-check:
	diff -q deploy/helm/dashboards/nv-config-manager-overview.json docs/assets/static/nv-config-manager-overview.json

docs-lint:
	@set -e; \
	echo "Linting documentation markdown with rumdl..."; \
	cd docs; \
	if command -v rumdl >/dev/null 2>&1; then \
		rumdl check --fail-on warning .; \
	else \
		npx --yes rumdl check --fail-on warning .; \
	fi

docs-format:
	@set -e; \
	echo "Formatting documentation markdown with rumdl..."; \
	cd docs; \
	if command -v rumdl >/dev/null 2>&1; then \
		rumdl fmt .; \
	else \
		npx --yes rumdl fmt .; \
	fi

docs-lint-fern:
	@set -e; \
	cd docs; \
	echo "Checking Fern configuration..."; \
	if command -v fern >/dev/null 2>&1; then \
		fern check --warnings; \
	else \
		npx --yes fern-api check --warnings; \
	fi; \
	echo ""; \
	echo "Checking Fern markdown..."; \
	if command -v fern >/dev/null 2>&1; then \
		fern docs md check; \
	else \
		npx --yes fern-api docs md check; \
	fi

docs-live:
	@set -e; \
	cd docs; \
	echo "Starting Fern docs dev server..."; \
	if command -v fern >/dev/null 2>&1; then \
		fern docs dev; \
	else \
		npx --yes fern-api docs dev; \
	fi

docs-preview:
	@set -e; \
	cd docs; \
	echo "Generating Fern docs preview..."; \
	if command -v fern >/dev/null 2>&1; then \
		fern generate --docs --preview; \
	else \
		npx --yes fern-api generate --docs --preview; \
	fi

docs-publish:
	@set -e; \
	cd docs; \
	echo "Publishing Fern docs..."; \
	if command -v fern >/dev/null 2>&1; then \
		fern generate --docs; \
	else \
		npx --yes fern-api generate --docs; \
	fi

docs-publish-in-ci:
	@set -e; \
	cd docs; \
	echo "Publishing Fern docs from CI..."; \
	if command -v fern >/dev/null 2>&1; then \
		fern generate --docs --force; \
	else \
		npx --yes fern-api generate --docs --force; \
	fi

docs-screenshots:
	cd installer && uv run python scripts/screenshot_tui.py

docs-air-sim-screenshots:
	cd installer && uv run python scripts/screenshot_air_sim_tui.py

docs-ui-screenshots:
	cd ui && npm run docs:screenshots

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "dist" -exec rm -rf {} + 2>/dev/null || true
	find . -mindepth 2 -type d -name "build" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".cache" -exec rm -rf {} + 2>/dev/null || true

# UI targets
ui-install:
	cd ui && npm install

ui-dev:
	cd ui && npm run dev

ui-build:
	cd ui && npm run build

ui-lint:
	cd ui && npm run lint

# =============================================================================
# Docker Build Targets
# =============================================================================

# Build all local Docker images
docker-build:
	@pids=""; \
	$(MAKE) docker-build-nv-config-manager & pids="$$pids $$!"; \
	$(MAKE) docker-build-kea & pids="$$pids $$!"; \
	$(MAKE) docker-build-kea-admin & pids="$$pids $$!"; \
	$(MAKE) docker-build-ui & pids="$$pids $$!"; \
	$(MAKE) docker-build-nb & pids="$$pids $$!"; \
	$(MAKE) docker-build-nats-ready & pids="$$pids $$!"; \
	$(MAKE) docker-build-temporal & pids="$$pids $$!"; \
	failed=0; \
	for pid in $$pids; do \
		if ! wait "$$pid"; then \
			echo "❌ Docker build process $$pid failed"; \
			failed=1; \
		fi; \
	done; \
	if [ "$$failed" -ne 0 ]; then \
		echo "❌ One or more Docker image builds failed"; \
		exit 1; \
	fi
	@echo "✅ All images built successfully"

# Build NVIDIA Config Manager services image
docker-build-nv-config-manager:
	@echo "🏗️  Building NVIDIA Config Manager services image with tag $(LOCAL_TAG)..."
	docker build --provenance=false $(APT_MIRROR_DEBIAN_ARGS) $(TEMPLATE_ENGINE_VERSION_ARG) $(NVCM_NUMPY_BUILD_ARGS) -t nv-config-manager:$(LOCAL_TAG) -f build/nv-config-manager.Dockerfile .
	@echo "✅ Built nv-config-manager:$(LOCAL_TAG)"

# Build NVIDIA Config Manager KEA DHCP server image
docker-build-kea:
	@echo "🏗️  Building NVIDIA Config Manager KEA DHCP image with tag $(LOCAL_TAG)..."
	docker build --provenance=false $(APT_MIRROR_ARGS) -t nv-config-manager-kea:$(LOCAL_TAG) -f build/kea.Dockerfile .
	@echo "✅ Built nv-config-manager-kea:$(LOCAL_TAG)"

# Build NVIDIA Config Manager KEA Admin image (for database schema initialization)
docker-build-kea-admin:
	@echo "🏗️  Building NVIDIA Config Manager KEA Admin image with tag $(LOCAL_TAG)..."
	docker build --provenance=false $(APT_MIRROR_ARGS) -t nv-config-manager-kea-admin:$(LOCAL_TAG) -f build/kea-admin.Dockerfile .
	@echo "✅ Built nv-config-manager-kea-admin:$(LOCAL_TAG)"

# Build NVIDIA Config Manager UI image
docker-build-ui:
	@echo "🏗️  Building NVIDIA Config Manager UI image with tag $(LOCAL_TAG)..."
	docker build --provenance=false -t nv-config-manager-ui:$(LOCAL_TAG) -f build/ui.Dockerfile ui/
	@echo "✅ Built nv-config-manager-ui:$(LOCAL_TAG)"

# Build Nautobot image
docker-build-nb:
	@echo "🏗️  Building Nautobot image with tag $(LOCAL_TAG)..."
	docker build --provenance=false $(APT_MIRROR_DEBIAN_ARGS) $(NAUTOBOT_APP_OVERLAYS_VERSION_ARG) $(NAUTOBOT_NV_CONFIG_MANAGER_VERSION_ARG) -t nv-config-manager-nautobot:$(LOCAL_TAG) -f build/nautobot.Dockerfile components/nautobot/
	@echo "✅ Built nv-config-manager-nautobot:$(LOCAL_TAG)"

# Build NATS-ready init container
docker-build-nats-ready:
	@echo "🏗️  Building NATS-ready init container with tag $(LOCAL_TAG)..."
	docker build --provenance=false $(APT_MIRROR_ARGS) -t nv-config-manager-nats-ready:$(LOCAL_TAG) -f build/nats-ready.Dockerfile components/nats-ready/
	@echo "✅ Built nv-config-manager-nats-ready:$(LOCAL_TAG)"

# Build project-owned Temporal server, bootstrap, and web UI images.
.PHONY: docker-build-temporal
docker-build-temporal:
	@echo "🏗️  Building distroless Temporal images with tag $(LOCAL_TAG)..."
	docker build --provenance=false $(TEMPORAL_BUILD_ARGS) --target server -t nv-config-manager-temporal:$(LOCAL_TAG) -f build/temporal.Dockerfile .
	docker build --provenance=false $(TEMPORAL_BUILD_ARGS) --target bootstrap -t nv-config-manager-temporal-bootstrap:$(LOCAL_TAG) -f build/temporal.Dockerfile .
	docker build --provenance=false $(TEMPORAL_BUILD_ARGS) --target ui -t nv-config-manager-temporal-ui:$(LOCAL_TAG) -f build/temporal.Dockerfile .
	@echo "✅ Built distroless Temporal images with tag $(LOCAL_TAG)"

# Push images to registry (requires REGISTRY env var)
REGISTRY ?= ghcr.io/your-org
VERSION ?= latest

docker-push:
	docker tag nv-config-manager:$(LOCAL_TAG) $(REGISTRY)/nv-config-manager:$(VERSION)
	docker tag nv-config-manager-ui:$(LOCAL_TAG) $(REGISTRY)/nv-config-manager-ui:$(VERSION)
	docker tag nv-config-manager-nautobot:$(LOCAL_TAG) $(REGISTRY)/nv-config-manager-nautobot:$(VERSION)
	docker tag nv-config-manager-nats-ready:$(LOCAL_TAG) $(REGISTRY)/nv-config-manager-nats-ready:$(VERSION)
	docker tag nv-config-manager-temporal:$(LOCAL_TAG) $(REGISTRY)/nv-config-manager-temporal:$(VERSION)
	docker tag nv-config-manager-temporal-bootstrap:$(LOCAL_TAG) $(REGISTRY)/nv-config-manager-temporal-bootstrap:$(VERSION)
	docker tag nv-config-manager-temporal-ui:$(LOCAL_TAG) $(REGISTRY)/nv-config-manager-temporal-ui:$(VERSION)
	docker push $(REGISTRY)/nv-config-manager:$(VERSION)
	docker push $(REGISTRY)/nv-config-manager-ui:$(VERSION)
	docker push $(REGISTRY)/nv-config-manager-nautobot:$(VERSION)
	docker push $(REGISTRY)/nv-config-manager-nats-ready:$(VERSION)
	docker push $(REGISTRY)/nv-config-manager-temporal:$(VERSION)
	docker push $(REGISTRY)/nv-config-manager-temporal-bootstrap:$(VERSION)
	docker push $(REGISTRY)/nv-config-manager-temporal-ui:$(VERSION)

# =============================================================================
# Multi-Arch Build Targets
# These targets build and push multi-arch images (amd64 + arm64)
# =============================================================================

PLATFORMS ?= linux/amd64,linux/arm64
# Single platform for native builds (set by CI for parallel arch builds)
PLATFORM ?= linux/amd64
# Extra tags can be passed via EXTRA_TAGS, e.g. EXTRA_TAGS="-t repo:tag1 -t repo:tag2"
EXTRA_TAGS ?=
# Buildx output for the single-arch targets. Default pushes to $(REGISTRY)
# (requires docker login). Credential-less builds override with e.g.
# DOCKER_BUILD_OUTPUT="--output type=docker,dest=/path/image.tar"
DOCKER_BUILD_OUTPUT ?= --push
# Set PUSH_LATEST=true to also push :latest tags (only for main branch releases)
PUSH_LATEST ?=
LATEST_TAG_nv_config_manager = $(if $(PUSH_LATEST),-t $(REGISTRY)/nv-config-manager:latest,)
LATEST_TAG_kea = $(if $(PUSH_LATEST),-t $(REGISTRY)/nv-config-manager-kea:latest,)
LATEST_TAG_kea_admin = $(if $(PUSH_LATEST),-t $(REGISTRY)/nv-config-manager-kea-admin:latest,)
LATEST_TAG_ui = $(if $(PUSH_LATEST),-t $(REGISTRY)/nv-config-manager-ui:latest,)
LATEST_TAG_nautobot = $(if $(PUSH_LATEST),-t $(REGISTRY)/nv-config-manager-nautobot:latest,)
LATEST_TAG_nats_ready = $(if $(PUSH_LATEST),-t $(REGISTRY)/nv-config-manager-nats-ready:latest,)
LATEST_TAG_temporal = $(if $(PUSH_LATEST),-t $(REGISTRY)/nv-config-manager-temporal:latest,)
LATEST_TAG_temporal_bootstrap = $(if $(PUSH_LATEST),-t $(REGISTRY)/nv-config-manager-temporal-bootstrap:latest,)
LATEST_TAG_temporal_ui = $(if $(PUSH_LATEST),-t $(REGISTRY)/nv-config-manager-temporal-ui:latest,)

# All image names for manifest operations
IMAGES := nv-config-manager nv-config-manager-kea nv-config-manager-kea-admin nv-config-manager-ui nv-config-manager-nautobot nv-config-manager-nats-ready nv-config-manager-temporal nv-config-manager-temporal-bootstrap nv-config-manager-temporal-ui

.PHONY: docker-buildx-setup
docker-buildx-setup: ## Sets up Docker Buildx for multi-arch builds.
	@echo "🔧 Setting up Docker Buildx for multi-arch builds..."
	@docker run --rm --privileged tonistiigi/binfmt --install all 2>/dev/null || true
	@docker buildx create --use --name multiarch --driver docker-container --driver-opt network=host --bootstrap 2>/dev/null || docker buildx use multiarch
	@echo "✅ Buildx ready for platforms: $(PLATFORMS)"

.PHONY: docker-buildx-setup-native
docker-buildx-setup-native: ## Sets up Docker Buildx for native single-arch builds (no QEMU).
	@echo "🔧 Setting up Docker Buildx for native builds..."
	@docker buildx create --use --name native-builder --driver docker-container --driver-opt network=host --bootstrap 2>/dev/null || docker buildx use native-builder
	@echo "✅ Buildx ready for native $(PLATFORM) builds"

.PHONY: docker-build-multiarch
docker-build-multiarch: docker-buildx-setup docker-build-nv-config-manager-multiarch docker-build-kea-multiarch docker-build-kea-admin-multiarch docker-build-ui-multiarch docker-build-nb-multiarch docker-build-nats-ready-multiarch docker-build-temporal-multiarch ## Builds and pushes all multi-arch images (requires registry login).
	@echo "✅ All multi-arch images built and pushed successfully"

# =============================================================================
# Single-Arch Build Targets (for parallel CI builds)
# Call docker-buildx-setup-native first for native builds on the target arch
# =============================================================================

.PHONY: docker-build-all
docker-build-all: ## Builds and pushes all images for PLATFORM in parallel. Call setup target first.
	@pids=""; \
	$(MAKE) docker-build-single-nv-config-manager & pids="$$pids $$!"; \
	$(MAKE) docker-build-single-kea & pids="$$pids $$!"; \
	$(MAKE) docker-build-single-kea-admin & pids="$$pids $$!"; \
	$(MAKE) docker-build-single-ui & pids="$$pids $$!"; \
	$(MAKE) docker-build-single-nb & pids="$$pids $$!"; \
	$(MAKE) docker-build-single-nats-ready & pids="$$pids $$!"; \
	$(MAKE) docker-build-single-temporal & pids="$$pids $$!"; \
	failed=0; \
	for pid in $$pids; do \
		if ! wait $$pid; then \
			echo "❌ Build process $$pid failed"; \
			failed=1; \
		fi; \
	done; \
	if [ $$failed -eq 1 ]; then \
		echo "❌ One or more builds failed"; \
		exit 1; \
	fi
	@echo "✅ All $(PLATFORM) images built and pushed successfully"

.PHONY: docker-build-single-nv-config-manager
docker-build-single-nv-config-manager: ## Builds and pushes NVIDIA Config Manager image for PLATFORM.
	@echo "🏗️  Building nv-config-manager image for $(PLATFORM)..."
	docker buildx build \
		--platform $(PLATFORM) \
		-t $(REGISTRY)/nv-config-manager:$(VERSION) \
		$(EXTRA_TAGS) \
		$(APT_MIRROR_DEBIAN_ARGS) \
		$(TEMPLATE_ENGINE_VERSION_ARG) \
		$(NVCM_NUMPY_BUILD_ARGS) \
		-f build/nv-config-manager.Dockerfile \
		$(DOCKER_BUILD_OUTPUT) \
		.
	@echo "✅ nv-config-manager:$(VERSION) built/exported ($(REGISTRY))"

.PHONY: docker-build-single-kea
docker-build-single-kea: ## Builds and pushes KEA image for PLATFORM.
	@echo "🏗️  Building nv-config-manager-kea image for $(PLATFORM)..."
	docker buildx build \
		--platform $(PLATFORM) \
		-t $(REGISTRY)/nv-config-manager-kea:$(VERSION) \
		$(EXTRA_TAGS) \
		$(APT_MIRROR_ARGS) \
		-f build/kea.Dockerfile \
		$(DOCKER_BUILD_OUTPUT) \
		.
	@echo "✅ nv-config-manager-kea:$(VERSION) built/exported ($(REGISTRY))"

.PHONY: docker-build-single-kea-admin
docker-build-single-kea-admin: ## Builds and pushes KEA Admin image for PLATFORM.
	@echo "🏗️  Building nv-config-manager-kea-admin image for $(PLATFORM)..."
	docker buildx build \
		--platform $(PLATFORM) \
		-t $(REGISTRY)/nv-config-manager-kea-admin:$(VERSION) \
		$(EXTRA_TAGS) \
		$(APT_MIRROR_ARGS) \
		-f build/kea-admin.Dockerfile \
		$(DOCKER_BUILD_OUTPUT) \
		.
	@echo "✅ nv-config-manager-kea-admin:$(VERSION) built/exported ($(REGISTRY))"

.PHONY: docker-build-single-ui
docker-build-single-ui: ## Builds and pushes UI image for PLATFORM.
	@echo "🏗️  Building nv-config-manager-ui image for $(PLATFORM)..."
	docker buildx build \
		--platform $(PLATFORM) \
		-t $(REGISTRY)/nv-config-manager-ui:$(VERSION) \
		$(EXTRA_TAGS) \
		-f build/ui.Dockerfile \
		$(DOCKER_BUILD_OUTPUT) \
		ui/
	@echo "✅ nv-config-manager-ui:$(VERSION) built/exported ($(REGISTRY))"

.PHONY: docker-build-single-nb
docker-build-single-nb: ## Builds and pushes Nautobot image for PLATFORM.
	@echo "🏗️  Building nv-config-manager-nautobot image for $(PLATFORM)..."
	docker buildx build \
		--platform $(PLATFORM) \
		-t $(REGISTRY)/nv-config-manager-nautobot:$(VERSION) \
		$(EXTRA_TAGS) \
		$(APT_MIRROR_DEBIAN_ARGS) \
		$(NAUTOBOT_APP_OVERLAYS_VERSION_ARG) \
		$(NAUTOBOT_NV_CONFIG_MANAGER_VERSION_ARG) \
		-f build/nautobot.Dockerfile \
		$(DOCKER_BUILD_OUTPUT) \
		components/nautobot/
	@echo "✅ nv-config-manager-nautobot:$(VERSION) built/exported ($(REGISTRY))"

.PHONY: docker-build-single-nats-ready
docker-build-single-nats-ready: ## Builds and pushes NATS-ready image for PLATFORM.
	@echo "🏗️  Building nv-config-manager-nats-ready image for $(PLATFORM)..."
	docker buildx build \
		--platform $(PLATFORM) \
		-t $(REGISTRY)/nv-config-manager-nats-ready:$(VERSION) \
		$(EXTRA_TAGS) \
		$(APT_MIRROR_ARGS) \
		-f build/nats-ready.Dockerfile \
		$(DOCKER_BUILD_OUTPUT) \
		components/nats-ready/
	@echo "✅ nv-config-manager-nats-ready:$(VERSION) built/exported ($(REGISTRY))"

# One buildx invocation per Temporal image so each maps 1:1 to a build target,
# matching the other docker-build-single-* recipes. This lets the secret-free
# PR build produce a single tarball per image via DOCKER_BUILD_OUTPUT; the
# aggregate target below preserves the push-all-three behavior for release/main.
.PHONY: docker-build-single-temporal
docker-build-single-temporal: docker-build-single-temporal-server docker-build-single-temporal-bootstrap docker-build-single-temporal-ui ## Builds/pushes all distroless Temporal images for PLATFORM.
	@echo "✅ Distroless Temporal images built for $(PLATFORM)"

.PHONY: docker-build-single-temporal-server
docker-build-single-temporal-server: ## Builds the distroless Temporal server image for PLATFORM.
	@echo "🏗️  Building distroless Temporal server image for $(PLATFORM)..."
	docker buildx build --platform $(PLATFORM) -t $(REGISTRY)/nv-config-manager-temporal:$(VERSION) $(LATEST_TAG_temporal) $(EXTRA_TAGS) $(TEMPORAL_BUILD_ARGS) --target server -f build/temporal.Dockerfile $(DOCKER_BUILD_OUTPUT) .

.PHONY: docker-build-single-temporal-bootstrap
docker-build-single-temporal-bootstrap: ## Builds the distroless Temporal bootstrap image for PLATFORM.
	@echo "🏗️  Building distroless Temporal bootstrap image for $(PLATFORM)..."
	docker buildx build --platform $(PLATFORM) -t $(REGISTRY)/nv-config-manager-temporal-bootstrap:$(VERSION) $(LATEST_TAG_temporal_bootstrap) $(EXTRA_TAGS) $(TEMPORAL_BUILD_ARGS) --target bootstrap -f build/temporal.Dockerfile $(DOCKER_BUILD_OUTPUT) .

.PHONY: docker-build-single-temporal-ui
docker-build-single-temporal-ui: ## Builds the distroless Temporal UI image for PLATFORM.
	@echo "🏗️  Building distroless Temporal UI image for $(PLATFORM)..."
	docker buildx build --platform $(PLATFORM) -t $(REGISTRY)/nv-config-manager-temporal-ui:$(VERSION) $(LATEST_TAG_temporal_ui) $(EXTRA_TAGS) $(TEMPORAL_BUILD_ARGS) --target ui -f build/temporal.Dockerfile $(DOCKER_BUILD_OUTPUT) .

# =============================================================================
# Manifest Merge Targets (for combining arch-specific images)
# =============================================================================

.PHONY: docker-manifest-create
docker-manifest-create: ## Creates multi-arch manifests from arch-specific images. Requires VERSION_BASE and VERSION_FINAL.
	@echo "🔗 Creating multi-arch manifests..."
	@for img in $(IMAGES); do \
		echo "  Creating manifest for $$img:$(VERSION_FINAL)..."; \
		docker buildx imagetools create \
			-t $(REGISTRY)/$$img:$(VERSION_FINAL) \
			$(REGISTRY)/$$img:$(VERSION_BASE)-amd64 \
			$(REGISTRY)/$$img:$(VERSION_BASE)-arm64; \
	done
	@echo "✅ All multi-arch manifests created with tag $(VERSION_FINAL)"

.PHONY: docker-manifest-create-latest
docker-manifest-create-latest: ## Creates multi-arch manifests with :latest tag. Requires VERSION_BASE.
	@echo "🔗 Creating multi-arch :latest manifests..."
	@for img in $(IMAGES); do \
		echo "  Creating manifest for $$img:latest..."; \
		docker buildx imagetools create \
			-t $(REGISTRY)/$$img:latest \
			$(REGISTRY)/$$img:$(VERSION_BASE)-amd64 \
			$(REGISTRY)/$$img:$(VERSION_BASE)-arm64; \
	done
	@echo "✅ All multi-arch :latest manifests created"

.PHONY: docker-build-nv-config-manager-multiarch
docker-build-nv-config-manager-multiarch: docker-buildx-setup ## Builds and pushes multi-arch NVIDIA Config Manager services image.
	@echo "🏗️  Building multi-arch nv-config-manager image..."
	docker buildx build \
		--platform $(PLATFORMS) \
		-t $(REGISTRY)/nv-config-manager:$(VERSION) \
		$(LATEST_TAG_nv_config_manager) \
		$(EXTRA_TAGS) \
		$(APT_MIRROR_DEBIAN_ARGS) \
		$(TEMPLATE_ENGINE_VERSION_ARG) \
		$(NVCM_NUMPY_BUILD_ARGS) \
		-f build/nv-config-manager.Dockerfile \
		--push \
		.
	@echo "✅ Multi-arch nv-config-manager pushed to $(REGISTRY)"

.PHONY: docker-build-kea-multiarch
docker-build-kea-multiarch: docker-buildx-setup ## Builds and pushes multi-arch NVIDIA Config Manager KEA DHCP image.
	@echo "🏗️  Building multi-arch nv-config-manager-kea image..."
	docker buildx build \
		--platform $(PLATFORMS) \
		-t $(REGISTRY)/nv-config-manager-kea:$(VERSION) \
		$(LATEST_TAG_kea) \
		$(EXTRA_TAGS) \
		$(APT_MIRROR_ARGS) \
		-f build/kea.Dockerfile \
		--push \
		.
	@echo "✅ Multi-arch nv-config-manager-kea pushed to $(REGISTRY)"

.PHONY: docker-build-kea-admin-multiarch
docker-build-kea-admin-multiarch: docker-buildx-setup ## Builds and pushes multi-arch NVIDIA Config Manager KEA Admin image.
	@echo "🏗️  Building multi-arch nv-config-manager-kea-admin image..."
	docker buildx build \
		--platform $(PLATFORMS) \
		-t $(REGISTRY)/nv-config-manager-kea-admin:$(VERSION) \
		$(LATEST_TAG_kea_admin) \
		$(EXTRA_TAGS) \
		$(APT_MIRROR_ARGS) \
		-f build/kea-admin.Dockerfile \
		--push \
		.
	@echo "✅ Multi-arch nv-config-manager-kea-admin pushed to $(REGISTRY)"

.PHONY: docker-build-ui-multiarch
docker-build-ui-multiarch: docker-buildx-setup ## Builds and pushes multi-arch NVIDIA Config Manager UI image.
	@echo "🏗️  Building multi-arch nv-config-manager-ui image..."
	docker buildx build \
		--platform $(PLATFORMS) \
		-t $(REGISTRY)/nv-config-manager-ui:$(VERSION) \
		$(LATEST_TAG_ui) \
		$(EXTRA_TAGS) \
		-f build/ui.Dockerfile \
		--push \
		ui/
	@echo "✅ Multi-arch nv-config-manager-ui pushed to $(REGISTRY)"

.PHONY: docker-build-nb-multiarch
docker-build-nb-multiarch: docker-buildx-setup ## Builds and pushes multi-arch Nautobot image.
	@echo "🏗️  Building multi-arch nv-config-manager-nautobot image..."
	docker buildx build \
		--platform $(PLATFORMS) \
		-t $(REGISTRY)/nv-config-manager-nautobot:$(VERSION) \
		$(LATEST_TAG_nautobot) \
		$(EXTRA_TAGS) \
		$(APT_MIRROR_DEBIAN_ARGS) \
		$(NAUTOBOT_APP_OVERLAYS_VERSION_ARG) \
		$(NAUTOBOT_NV_CONFIG_MANAGER_VERSION_ARG) \
		-f build/nautobot.Dockerfile \
		--push \
		components/nautobot/
	@echo "✅ Multi-arch nv-config-manager-nautobot pushed to $(REGISTRY)"

.PHONY: docker-build-nats-ready-multiarch
docker-build-nats-ready-multiarch: docker-buildx-setup ## Builds and pushes multi-arch NATS-ready init container.
	@echo "🏗️  Building multi-arch nv-config-manager-nats-ready image..."
	docker buildx build \
		--platform $(PLATFORMS) \
		-t $(REGISTRY)/nv-config-manager-nats-ready:$(VERSION) \
		$(LATEST_TAG_nats_ready) \
		$(EXTRA_TAGS) \
		$(APT_MIRROR_ARGS) \
		-f build/nats-ready.Dockerfile \
		--push \
		components/nats-ready/
	@echo "✅ Multi-arch nv-config-manager-nats-ready pushed to $(REGISTRY)"

.PHONY: docker-build-temporal-multiarch
docker-build-temporal-multiarch: docker-buildx-setup ## Builds and pushes multi-arch distroless Temporal images.
	@echo "🏗️  Building multi-arch distroless Temporal images..."
	docker buildx build --platform $(PLATFORMS) -t $(REGISTRY)/nv-config-manager-temporal:$(VERSION) $(LATEST_TAG_temporal) $(EXTRA_TAGS) $(TEMPORAL_BUILD_ARGS) --target server -f build/temporal.Dockerfile --push .
	docker buildx build --platform $(PLATFORMS) -t $(REGISTRY)/nv-config-manager-temporal-bootstrap:$(VERSION) $(LATEST_TAG_temporal_bootstrap) $(EXTRA_TAGS) $(TEMPORAL_BUILD_ARGS) --target bootstrap -f build/temporal.Dockerfile --push .
	docker buildx build --platform $(PLATFORMS) -t $(REGISTRY)/nv-config-manager-temporal-ui:$(VERSION) $(LATEST_TAG_temporal_ui) $(EXTRA_TAGS) $(TEMPORAL_BUILD_ARGS) --target ui -f build/temporal.Dockerfile --push .
	@echo "✅ Multi-arch distroless Temporal images pushed to $(REGISTRY)"

# =============================================================================
# Service run targets (for local development without k8s)
# =============================================================================
run-ztp-api:
	uv run uvicorn nv_config_manager.ztp.api.main:app --loop asyncio --reload --port 8080

run-dhcp-api:
	uv run uvicorn nv_config_manager.dhcp.api:app --loop asyncio --reload --port 8081

run-temporal-api:
	uv run uvicorn nv_config_manager.temporal.api.main:app --loop asyncio --reload --port 8082

run-temporal-worker:
	uv run python -m nv_config_manager.temporal.worker.main

run-render-api:
	uv run uvicorn nv_config_manager.render.api.main:app --loop asyncio --reload --port 8083

run-config-store-api:
	uv run uvicorn nv_config_manager.config_store.api.main:app --loop asyncio --reload --port 8084

# Database migrations
db-migrate:
	uv run alembic -c db/alembic.ini upgrade head

db-rollback:
	uv run alembic -c db/alembic.ini downgrade -1

db-generate:
	@read -p "Migration message: " msg; \
	uv run alembic -c db/alembic.ini revision --autogenerate -m "$$msg"

# =============================================================================
# Local Kubernetes Deployment (via nv-config-manager-installer)
# =============================================================================

# Build local images and deploy to local kubernetes (no Kind)
# Uses the same config profile as kind-up.
# Override INSTALL_CONFIG with a copied installer config when needed.
local-up:
	@echo "🚀 Deploying NVIDIA Config Manager with installer (config: $(INSTALL_CONFIG))..."
	cd installer && uv run nv-config-manager-installer deploy ../$(INSTALL_CONFIG) \
		--image-source local \
		--build-images \
		--install-envoy-gateway \
		--install-cnpg-operator \
		--install-cert-manager

# Deploy with Kind cluster (builds, loads to Kind, deploys via nv-config-manager-installer)
# Uses a pre-built config profile (default: local-superpod.yaml).
# Override INSTALL_CONFIG with a copied installer config when needed.
HELM_TIMEOUT ?= 15m
HELM_DEBUG ?=
HELM_DEBUG_FLAG = $(if $(HELM_DEBUG),--helm-debug,)

kind-up:
	@echo "🚀 Deploying NVIDIA Config Manager with installer to Kind (config: $(INSTALL_CONFIG))..."
	@if ! kind get clusters 2>/dev/null | grep -q "^$(KIND_CLUSTER_NAME)$$"; then \
		echo "Creating Kind cluster: $(KIND_CLUSTER_NAME)"; \
		kind create cluster --name $(KIND_CLUSTER_NAME) --config $(KIND_CONFIG) --wait 5m; \
	fi
	cd installer && uv run nv-config-manager-installer deploy ../$(INSTALL_CONFIG) \
		--image-source local \
		--build-images \
		--load-kind \
		--kind-cluster $(KIND_CLUSTER_NAME) \
		--install-envoy-gateway \
		--install-cnpg-operator \
		--install-cert-manager \
		$(HELM_DEBUG_FLAG) --helm-timeout $(HELM_TIMEOUT)

# Deploy with Kind plus local Keycloak SSO, SPIRE SPIFFE, and workflow RBAC.
kind-up-sec:
	$(MAKE) kind-up-secure KIND_SEC_GATEWAY_CONTROLLER=envoyGateway

# Same secured local deployment, using kgateway instead of Envoy Gateway.
kind-up-sec-kgateway:
	$(MAKE) kind-up-secure KIND_SEC_GATEWAY_CONTROLLER=kgateway

kind-up-secure:
	@echo "🚀 Deploying NVIDIA Config Manager with local security stack and $(KIND_SEC_GATEWAY_CONTROLLER) to Kind (config: $(KIND_SEC_INSTALL_CONFIG))..."
	@if ! kind get clusters 2>/dev/null | grep -q "^$(KIND_CLUSTER_NAME)$$"; then \
		echo "Creating Kind cluster: $(KIND_CLUSTER_NAME)"; \
		kind create cluster --name $(KIND_CLUSTER_NAME) --config $(KIND_CONFIG) --wait 5m; \
	fi
	kind export kubeconfig --name $(KIND_CLUSTER_NAME)
	@# ``@``-prefixed so make does not echo the expanded recipe: these commands
	@# carry --keycloak-admin-password / --oidc-client-secret, and make's default
	@# command echo would print them verbatim into the (public) CI log. They are
	@# dev defaults today, but suppressing keeps real creds out of logs if these
	@# vars are ever overridden. The scripts still print their own progress.
	@echo "🔐 Installing security dependencies (keycloak / SPIRE / gateway)..."
	@./scripts/install-security-dependencies \
		--gateway-controller $(KIND_SEC_GATEWAY_CONTROLLER) \
		--cluster-name $(KIND_CLUSTER_NAME) \
		--app-namespace $(KIND_SEC_NAMESPACE) \
		--base-hostname $(KIND_SEC_HOSTNAME) \
		--keycloak-hostname $(KIND_SEC_KEYCLOAK_HOSTNAME) \
		--spiffe-trust-domain $(KIND_SEC_SPIFFE_TRUST_DOMAIN) \
		--keycloak-admin-password $(KIND_SEC_KEYCLOAK_ADMIN_PASSWORD) \
		--oidc-client-secret $(KIND_SEC_OIDC_CLIENT_SECRET) \
		--helm-timeout $(HELM_TIMEOUT)
	@echo "🔧 Rendering local security config..."
	@uv run python scripts/render-local-security-config \
		--gateway $(KIND_SEC_GATEWAY_CONTROLLER) \
		--input $(KIND_SEC_INSTALL_CONFIG) \
		--output $(abspath $(KIND_SEC_RENDERED_CONFIG)) \
		--namespace $(KIND_SEC_NAMESPACE) \
		--release-name $(RELEASE_NAME) \
		--hostname $(KIND_SEC_HOSTNAME) \
		--keycloak-hostname $(KIND_SEC_KEYCLOAK_HOSTNAME) \
		--spiffe-trust-domain $(KIND_SEC_SPIFFE_TRUST_DOMAIN) \
		--oidc-client-secret $(KIND_SEC_OIDC_CLIENT_SECRET)
	cd installer && uv run nv-config-manager-installer deploy $(abspath $(KIND_SEC_RENDERED_CONFIG)) \
		--image-source local \
		--build-images \
		--load-kind \
		--kind-cluster $(KIND_CLUSTER_NAME) \
		$(if $(filter envoyGateway,$(KIND_SEC_GATEWAY_CONTROLLER)),--install-envoy-gateway) --install-cnpg-operator \
		--install-cert-manager \
		$(HELM_DEBUG_FLAG) --helm-timeout $(HELM_TIMEOUT)
	./scripts/create-local-security-nautobot-users \
		--namespace $(KIND_SEC_NAMESPACE) \
		--release-name $(RELEASE_NAME)

# Seed Temporal with many non-terminal workflows to test workflow list latency.
workflow-perf-seed:
	@echo "🌱 Seeding workflow latency fixtures (pending: $(WORKFLOW_PERF_COUNT), running non-pending: $(WORKFLOW_PERF_RUNNING_COUNT), failed: $(WORKFLOW_PERF_FAILED_COUNT))..."
	uv run scripts/seed-workflow-latency-data \
		--port-forward \
		--kube-namespace $(NAMESPACE) \
		--release-name $(RELEASE_NAME) \
		--count $(WORKFLOW_PERF_COUNT) \
		--running-count $(WORKFLOW_PERF_RUNNING_COUNT) \
		--failed-count $(WORKFLOW_PERF_FAILED_COUNT)

# Create Kind cluster, deploy NVIDIA Config Manager, and populate with mock topology.
# The topology job is declared in the config profile's content.run_after_deploy,
# so this is equivalent to kind-up when using a config with topology jobs.
kind-up-with-topology: kind-up

# Delete Kind cluster
kind-down:
	@echo "🗑️  Deleting Kind cluster: $(KIND_CLUSTER_NAME)..."
	kind delete cluster --name $(KIND_CLUSTER_NAME)

# Populate Nautobot with mock topology (standalone, for existing deployments)
# Uses a minimal config that only runs the topology job.
topology:
	@echo "🌐 Deploying mock topology jobs and creating test topology..."
	cd installer && uv run nv-config-manager-installer deploy ../$(INSTALL_CONFIG)

# Install the local gateway CA certificate into macOS/Linux, browser, and Node.js trust stores.
# This target is deliberately restricted to the configured local Kind cluster.
install-cert:
	@CERT_TMP=$$(mktemp); \
	trap "rm -f $$CERT_TMP" EXIT INT TERM; \
	if [ "$$(id -u)" -eq 0 ]; then \
		echo "Error: run 'make install-cert' as your regular user; it prompts for sudo only when needed." >&2; \
		exit 1; \
	fi; \
	if [ "$$(kubectl config current-context 2>/dev/null)" != "kind-$(KIND_CLUSTER_NAME)" ]; then \
		echo "Error: install-cert only trusts certificates from the local Kind context kind-$(KIND_CLUSTER_NAME)." >&2; \
		exit 1; \
	fi; \
	NVCM_OS=$$(uname); \
	NVCM_CERT_USER=$$(id -un); \
	case "$$NVCM_OS" in \
		Darwin) NVCM_USER_HOME=$$(dscl . -read "/Users/$$NVCM_CERT_USER" NFSHomeDirectory 2>/dev/null | awk 'NR == 1 {print $$2}') ;; \
		Linux) \
			if ! command -v getent >/dev/null; then \
				echo "Error: getent is required to locate your home directory." >&2; exit 1; \
			fi; \
			if ! command -v certutil >/dev/null; then \
				echo "Error: certutil is required to trust the local CA in Chrome/Chromium." >&2; \
				echo "Install libnss3-tools (Debian/Ubuntu) or nss-tools (RHEL/Fedora), then retry." >&2; exit 1; \
			fi; \
			NVCM_USER_HOME=$$(getent passwd "$$NVCM_CERT_USER" | cut -d: -f6) ;; \
		*) echo "Unsupported OS: install the local CA manually into your trust stores." >&2; exit 1 ;; \
	esac; \
	if [ -z "$$NVCM_USER_HOME" ]; then \
		echo "Error: could not determine your home directory." >&2; exit 1; \
	fi; \
	NVCM_CA_DIR="$$NVCM_USER_HOME/.config/nv-config-manager/certs"; \
	NVCM_CA_FILE="$$NVCM_CA_DIR/$(KIND_SEC_HOSTNAME)-ca.crt"; \
	NVCM_NSS_DB="$$NVCM_USER_HOME/.pki/nssdb"; \
	echo "Extracting local gateway CA certificate..."; \
	if ! CERT_DATA=$$(kubectl get secret -n $(KIND_SEC_NAMESPACE) $(KIND_SEC_GATEWAY_CA_SECRET) \
		-o jsonpath='{.data.tls\.crt}'); then \
		echo "Error: local gateway CA secret $(KIND_SEC_GATEWAY_CA_SECRET) was not found." >&2; \
		echo "Run make kind-up or make kind-up-sec to deploy the local CA, then retry." >&2; exit 1; \
	fi; \
	if [ -z "$$CERT_DATA" ]; then \
		echo "Error: local gateway CA secret does not contain a certificate." >&2; exit 1; \
	fi; \
	if ! printf '%s' "$$CERT_DATA" | base64 -d > "$$CERT_TMP" 2>/dev/null; then \
		if ! printf '%s' "$$CERT_DATA" | base64 -D > "$$CERT_TMP" 2>/dev/null; then \
			echo "Error: could not decode the local gateway CA certificate." >&2; exit 1; \
		fi; \
	fi; \
	if [ ! -s "$$CERT_TMP" ]; then \
		echo "Error: local gateway CA secret does not contain a certificate." >&2; exit 1; \
	fi; \
	if ! openssl x509 -in "$$CERT_TMP" -noout -text | grep -q 'CA:TRUE'; then \
		echo "Error: local gateway CA secret does not contain a CA certificate." >&2; \
		exit 1; \
	fi; \
	NVCM_CERT_SUBJECT=$$(openssl x509 -in "$$CERT_TMP" -noout -subject -nameopt RFC2253 | sed 's/^subject=//'); \
	NVCM_CERT_ISSUER=$$(openssl x509 -in "$$CERT_TMP" -noout -issuer -nameopt RFC2253 | sed 's/^issuer=//'); \
	if [ "$$NVCM_CERT_SUBJECT" != "CN=$(KIND_SEC_HOSTNAME) local development CA" ] || [ "$$NVCM_CERT_ISSUER" != "$$NVCM_CERT_SUBJECT" ]; then \
		echo "Error: refusing to trust a certificate that is not the expected local development CA." >&2; \
		exit 1; \
	fi; \
	mkdir -p "$$NVCM_CA_DIR"; \
	cp "$$CERT_TMP" "$$NVCM_CA_FILE"; \
	chmod 0644 "$$NVCM_CA_FILE"; \
	echo "Installing CA certificate (sudo required)..."; \
	if [ "$$NVCM_OS" = "Darwin" ]; then \
		sudo security add-trusted-cert -d -r trustRoot \
			-k /Library/Keychains/System.keychain "$$CERT_TMP"; \
	elif [ -d /usr/local/share/ca-certificates ]; then \
		sudo cp "$$CERT_TMP" /usr/local/share/ca-certificates/nvcm-gateway-ca.crt && \
		sudo update-ca-certificates; \
	elif [ -d /etc/pki/ca-trust/source/anchors ]; then \
		sudo cp "$$CERT_TMP" /etc/pki/ca-trust/source/anchors/nvcm-gateway-ca.crt && \
		sudo update-ca-trust; \
	else \
		echo "Unsupported OS: install the gateway cert manually into your trust store"; exit 1; \
	fi; \
	if [ "$$NVCM_OS" = "Linux" ]; then \
		if [ ! -f "$$NVCM_NSS_DB/cert9.db" ]; then \
			mkdir -p "$$NVCM_NSS_DB"; \
			certutil -N --empty-password -d "sql:$$NVCM_NSS_DB"; \
		fi; \
		certutil -D -d "sql:$$NVCM_NSS_DB" -n "NVCM Local Gateway CA" >/dev/null 2>&1 || true; \
		certutil -A -d "sql:$$NVCM_NSS_DB" -n "NVCM Local Gateway CA" -t "C,," -i "$$CERT_TMP"; \
	fi; \
	echo "CA certificate installed in the system and Chrome/Chromium trust stores."; \
	echo "Restart your browser to pick up the new trust anchor."; \
	echo "For Node.js tools, use: NODE_EXTRA_CA_CERTS=$$NVCM_CA_FILE claude mcp login nv-config-manager"

# Remove local deployment (preserves shared operators)
local-down:
	@echo "🗑️  Removing NVIDIA Config Manager from local Kubernetes..."
	helm uninstall $(RELEASE_NAME) -n $(NAMESPACE) 2>/dev/null || true
	kubectl delete namespace $(NAMESPACE) 2>/dev/null || true
	@echo "✅ Done."
	@echo ""
	@echo "ℹ️  Operators (Envoy Gateway, CNPG, cert-manager) are still running."
	@echo "ℹ️  To completely remove everything, run: make local-destroy"

# Complete cleanup including shared operators
local-destroy:
	@echo "⚠️  WARNING: This will remove operators that may be shared with other applications!"
	@echo "⚠️  This will delete: envoy-gateway-system, cnpg-system, cert-manager namespaces"
	@echo ""
	@read -p "Are you sure? [y/N] " -n 1 -r; \
	echo; \
	if [[ $$REPLY =~ ^[Yy]$$ ]]; then \
		echo "🗑️  Removing NVIDIA Config Manager platform..."; \
		helm uninstall $(RELEASE_NAME) -n $(NAMESPACE) 2>/dev/null || true; \
		kubectl delete namespace $(NAMESPACE) 2>/dev/null || true; \
		echo "🗑️  Removing operators and their namespaces..."; \
		helm uninstall envoy-gateway -n envoy-gateway-system 2>/dev/null || true; \
		helm uninstall eg -n envoy-gateway-system 2>/dev/null || true; \
		helm uninstall cnpg -n cnpg-system 2>/dev/null || true; \
		helm uninstall cert-manager -n cert-manager 2>/dev/null || true; \
		kubectl delete namespace envoy-gateway-system 2>/dev/null || true; \
		kubectl delete namespace cnpg-system 2>/dev/null || true; \
		kubectl delete namespace cert-manager 2>/dev/null || true; \
		echo "✅ Complete cleanup done."; \
	else \
		echo "❌ Cancelled."; \
	fi

# Show status of local deployment
local-status:
	@echo "=== NVIDIA Config Manager Deployment Status ==="
	@kubectl get pods -n $(NAMESPACE) -o wide 2>/dev/null || echo "Namespace $(NAMESPACE) not found"
	@echo ""
	@echo "=== Services ==="
	@kubectl get svc -n $(NAMESPACE) 2>/dev/null || true
	@echo ""
	@echo "=== Gateway ==="
	@kubectl get gateway -n $(NAMESPACE) 2>/dev/null || true

# Tail logs from all services
local-logs:
	kubectl logs -n $(NAMESPACE) -l app.kubernetes.io/instance=$(RELEASE_NAME) -f --all-containers --max-log-requests=20

# Restart all deployments (useful after config changes)
local-restart:
	kubectl rollout restart deployment -n $(NAMESPACE) -l app.kubernetes.io/instance=$(RELEASE_NAME)

# Port forward common services for local access
port-forward:
	@echo "Setting up port forwarding..."
	@echo "  Nautobot:     http://localhost:8080"
	@echo "  Temporal UI:  http://localhost:8081"
	@echo "Press Ctrl+C to stop"
	@kubectl port-forward -n $(NAMESPACE) svc/nautobot 8080:80 & \
	kubectl port-forward -n $(NAMESPACE) svc/temporal-ui 8081:8080 & \
	wait

# =============================================================================
# Observability port-forwards (local-dev stack only)
#
# Each target only forwards if the corresponding Service exists in the release
# namespace. The stack is rendered when installer config enables
# infrastructure.monitoring.observability_enabled (TUI: Infrastructure ->
# Enable local observability stack). For shared/prod clusters none of these services exist and the targets
# will print a friendly skip message instead of erroring.
# =============================================================================

# Foreground single-service port-forwards (Ctrl-C to stop).
obs-grafana:
	@kubectl get svc -n $(NAMESPACE) grafana >/dev/null 2>&1 || { echo "❌ svc/grafana not found in '$(NAMESPACE)' — enable observability in installer config"; exit 1; }
	@echo "🔭 Grafana    -> http://localhost:3000   (admin/admin)"
	kubectl port-forward -n $(NAMESPACE) svc/grafana 3000:80

obs-prometheus:
	@kubectl get svc -n $(NAMESPACE) prometheus-server >/dev/null 2>&1 || { echo "❌ svc/prometheus-server not found in '$(NAMESPACE)' — enable observability in installer config"; exit 1; }
	@echo "🔭 Prometheus -> http://localhost:9090"
	kubectl port-forward -n $(NAMESPACE) svc/prometheus-server 9090:9090

obs-loki:
	@kubectl get svc -n $(NAMESPACE) loki >/dev/null 2>&1 || { echo "❌ svc/loki not found in '$(NAMESPACE)' — enable observability in installer config"; exit 1; }
	@echo "🔭 Loki       -> http://localhost:3100"
	kubectl port-forward -n $(NAMESPACE) svc/loki 3100:3100

obs-alloy:
	@kubectl get svc -n $(NAMESPACE) alloy >/dev/null 2>&1 || { echo "❌ svc/alloy not found in '$(NAMESPACE)' — enable observability in installer config"; exit 1; }
	@echo "🔭 Alloy UI   -> http://localhost:12345"
	kubectl port-forward -n $(NAMESPACE) svc/alloy 12345:12345

# Run all four in parallel in the foreground. Ctrl-C kills the whole group.
obs-port-forward:
	@kubectl get svc -n $(NAMESPACE) grafana prometheus-server loki alloy >/dev/null 2>&1 || { echo "❌ Observability services not found in '$(NAMESPACE)' — enable observability in installer config"; exit 1; }
	@echo "🔭 Grafana    -> http://localhost:3000   (admin/admin)"
	@echo "🔭 Prometheus -> http://localhost:9090"
	@echo "🔭 Loki       -> http://localhost:3100"
	@echo "🔭 Alloy UI   -> http://localhost:12345"
	@echo "Press Ctrl+C to stop all forwards."
	@trap 'kill 0' INT TERM EXIT; \
	  kubectl port-forward -n $(NAMESPACE) svc/grafana 3000:80 & \
	  kubectl port-forward -n $(NAMESPACE) svc/prometheus-server 9090:9090 & \
	  kubectl port-forward -n $(NAMESPACE) svc/loki 3100:3100 & \
	  kubectl port-forward -n $(NAMESPACE) svc/alloy 12345:12345 & \
	  wait

# Best-effort cleanup if you ran `obs-port-forward` from a script and lost the
# foreground handle. Matches kubectl port-forwards targeting these four svcs.
obs-port-forward-stop:
	@pkill -f 'kubectl port-forward.*svc/(grafana|prometheus-server|loki|alloy)' 2>/dev/null && \
	  echo "✅ Stopped observability port-forwards" || \
	  echo "ℹ️  No matching port-forward processes were running"

# =============================================================================
# Production Deployment (via nv-config-manager-installer)
# =============================================================================

deploy:
	@echo "Running production deployment..."
	@echo ""
	@echo "Usage: cd installer && uv run nv-config-manager-installer deploy <config.yaml> [options]"
	@echo ""
	@echo "Examples:"
	@echo "  # Interactive TUI wizard"
	@echo "  cd installer && uv run nv-config-manager-installer"
	@echo ""
	@echo "  # Headless deploy from config"
	@echo "  cd installer && uv run nv-config-manager-installer deploy ../deploy/configs/local-superpod.yaml --build-images"
	@echo ""
	@echo "  # Local development with Kind"
	@echo "  make kind-up"
	@echo ""
	@echo "See installer/README.md for all options"
