#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
chart="${repo_root}/deploy/helm"
namespace="nvcm-shard-warning-$(date +%s)-$$"
test_chart="$(mktemp -d)"
output="$(mktemp)"
trap 'rm -rf "$test_chart"; rm -f "$output"; kubectl delete namespace "$namespace" --ignore-not-found --wait=false >/dev/null 2>&1 || true' EXIT

mkdir -p "${test_chart}/templates"
cat >"${test_chart}/Chart.yaml" <<'EOF'
apiVersion: v2
name: nv-config-manager
type: application
version: 0.1.0
EOF
cp "${chart}/templates/_helpers.tpl" "${test_chart}/templates/_helpers.tpl"
cp "${chart}/templates/NOTES.txt" "${test_chart}/templates/NOTES.txt"

kubectl create namespace "$namespace" >/dev/null
kubectl create configmap test-nv-config-manager-temporal-config \
    --namespace "$namespace" \
    --from-literal=config_template.yaml=$'persistence:\n  numHistoryShards: 16\n' \
    >/dev/null

render_notes() {
    local requested_shards="$1"

    helm install test "$test_chart" \
        --namespace "$namespace" \
        --set "global.namespace=${namespace}" \
        --set temporal.enabled=true \
        --set temporal.server.enabled=true \
        --set temporal.services.history.replicas=16 \
        --set "temporal.numHistoryShards=${requested_shards}" \
        --dry-run=server >"$output"
}

render_notes 128
if ! grep -Fq "WARNING: Temporal history shard count change detected" "$output"; then
    echo "expected a warning when requested shards differ from the installed count" >&2
    exit 1
fi
if ! grep -Fq "Installed shard count: 16" "$output"; then
    echo "expected the warning to report the installed shard count" >&2
    exit 1
fi
if ! grep -Fq "Requested shard count: 128" "$output"; then
    echo "expected the warning to report the requested shard count" >&2
    exit 1
fi

render_notes 16
if grep -Fq "WARNING: Temporal history shard count change detected" "$output"; then
    echo "did not expect a warning when the shard count is unchanged" >&2
    exit 1
fi

kubectl delete configmap test-nv-config-manager-temporal-config \
    --namespace "$namespace" >/dev/null
kubectl create configmap test-nv-config-manager-temporal-config \
    --namespace "$namespace" \
    --from-literal=config_template.yaml=$'persistence: invalid\n' \
    >/dev/null
render_notes 128
if grep -Fq "WARNING: Temporal history shard count change detected" "$output"; then
    echo "did not expect a warning when the installed configuration cannot be parsed" >&2
    exit 1
fi

echo "Temporal history shard warning tests passed"
