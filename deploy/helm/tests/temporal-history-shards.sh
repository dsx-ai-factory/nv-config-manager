#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
chart="${repo_root}/deploy/helm"
values="${chart}/values-ci.yaml"
rendered="$(mktemp)"
trap 'rm -f "$rendered"' EXIT

assert_temporal_config() {
    local expected_shards="$1"

    if ! awk -v expected_shards="$expected_shards" '
        BEGIN { RS = "---"; found = 0 }
        /kind: ConfigMap/ &&
        /name: test-nv-config-manager-temporal-config/ &&
        $0 ~ "numHistoryShards: " expected_shards "([[:space:]]|$)" {
            found = 1
        }
        END { exit(found ? 0 : 1) }
    ' "$rendered"; then
        echo "expected Temporal config to render numHistoryShards: ${expected_shards}" >&2
        exit 1
    fi
}

assert_history_replicas() {
    local expected_replicas="$1"

    if ! awk -v expected_replicas="$expected_replicas" '
        BEGIN { RS = "---"; found = 0 }
        /kind: Deployment/ &&
        /name: test-nv-config-manager-temporal-history/ &&
        $0 ~ "replicas: " expected_replicas "([[:space:]]|$)" {
            found = 1
        }
        END { exit(found ? 0 : 1) }
    ' "$rendered"; then
        echo "expected Temporal history Deployment to render ${expected_replicas} replicas" >&2
        exit 1
    fi
}

# Existing installations that omit numHistoryShards retain the legacy behavior.
helm template test "$chart" \
    --values "$values" \
    --set temporal.services.history.replicas=16 \
    --show-only templates/temporal.yaml >"$rendered"
assert_temporal_config 16
assert_history_replicas 16

# An explicit shard count is independent from the number of history pods.
helm template test "$chart" \
    --values "$values" \
    --set temporal.services.history.replicas=16 \
    --set temporal.numHistoryShards=128 \
    --show-only templates/temporal.yaml >"$rendered"
assert_temporal_config 128
assert_history_replicas 16

echo "Temporal history shard render tests passed"
