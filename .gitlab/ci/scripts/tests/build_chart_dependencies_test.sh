#!/usr/bin/env bash
# build_chart_dependencies.sh adds the private Template Preview chart only in trusted internal jobs.
# `helm` is replaced by a recorder, so the tests check what the script does and what it asks helm to do.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
script="${repo_root}/.gitlab/ci/scripts/build_chart_dependencies.sh"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

mkdir -p "$work/bin"
cat > "$work/bin/helm" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$HELM_LOG"
# `helm repo add ... --password-stdin` reads the token from stdin; record that it was NOT an argument.
if [[ "$*" == *--password-stdin* ]]; then cat > "$HELM_STDIN"; fi
EOF
chmod +x "$work/bin/helm"
export PATH="$work/bin:$PATH" HELM_LOG="$work/helm.log" HELM_STDIN="$work/helm.stdin"

failures=0
pass() { echo "PASS  $1"; }
fail() { echo "FAIL  $1"; failures=$((failures + 1)); }
check() { if "${@:2}"; then pass "$1"; else fail "$1"; fi; }

new_chart() { # new_chart NAME [GATE_ENABLED] -> a chart directory with the public Chart.yaml and values
    local dir="$work/$1"
    mkdir -p "$dir"
    cat > "$dir/Chart.yaml" <<'EOF'
apiVersion: v2
name: nv-config-manager
version: 0.0.0
dependencies:
  - name: tempo
    version: "1.24.4"
    repository: "https://grafana.github.io/helm-charts"
    condition: tempo.enabled
EOF
    cat > "$dir/values.yaml" <<EOF
templatePreview:
  enabled: false
  unsafeDynamicTemplatePlugins:
    enabled: ${2:-false}
    acknowledgeArbitraryCodeExecution: ${2:-false}
EOF
    echo "$dir"
}

run() { # run CHART_DIR [VAR=value ...] -> exit status of the script in a clean environment
    local dir="$1"; shift
    : > "$HELM_LOG"; : > "$HELM_STDIN"
    env -i PATH="$PATH" HOME="$work" HELM_LOG="$HELM_LOG" HELM_STDIN="$HELM_STDIN" "$@" \
        bash "$script" "$dir" > "$work/out.log" 2>&1
}

TRUSTED=(GITLAB_CI=true CI_COMMIT_REF_PROTECTED=true CI_PIPELINE_SOURCE=push)
OPT_IN=(NVCM_TEMPLATE_PREVIEW_CHART_REPOSITORY=https://charts.example/internal NVCM_TEMPLATE_PREVIEW_CHART_VERSION=0.7.0)
has_dep() { yq -e '.dependencies[] | select(.name == "nvcm-template-preview")' "$1/Chart.yaml" > /dev/null 2>&1; }
has_named() { yq -e ".dependencies[] | select(.name == \"$2\")" "$1/Chart.yaml" > /dev/null 2>&1; }
lacks_dep() { ! has_dep "$1"; }
helm_called() { grep -q -- "$1" "$HELM_LOG"; }
no_helm() { [[ ! -s "$HELM_LOG" ]]; }

# 1. Not opted in (a fork, the public pipeline, a pull request): a plain `helm dependency build`, nothing added.
dir="$(new_chart plain)"
run "$dir" "${TRUSTED[@]}" || true
check "without the variables the dependencies are built as before" helm_called "dependency build $dir"
check "without the variables Chart.yaml is untouched" lacks_dep "$dir"

# 2. Opted in but not a trusted pipeline: refuse, and do not touch the chart or call helm.
dir="$(new_chart unprotected)"
if run "$dir" GITLAB_CI=true CI_COMMIT_REF_PROTECTED=false CI_PIPELINE_SOURCE=push "${OPT_IN[@]}"; then fail "an unprotected ref is refused"; else pass "an unprotected ref is refused"; fi
check "an unprotected ref leaves Chart.yaml alone" lacks_dep "$dir"
check "an unprotected ref does not call helm" no_helm

dir="$(new_chart mr)"
if run "$dir" GITLAB_CI=true CI_COMMIT_REF_PROTECTED=true CI_PIPELINE_SOURCE=merge_request_event "${OPT_IN[@]}"; then fail "a merge request pipeline is refused"; else pass "a merge request pipeline is refused"; fi
check "a merge request pipeline does not call helm" no_helm

# 3. Opted in, trusted, but the shipped values would turn dynamic plugins on: refuse.
dir="$(new_chart gate-on true)"
if run "$dir" "${TRUSTED[@]}" "${OPT_IN[@]}"; then fail "values that enable dynamic plugins are refused"; else pass "values that enable dynamic plugins are refused"; fi
check "the refusal says why" grep -q "unsafeDynamicTemplatePlugins" "$work/out.log"
check "enabled dynamic plugins leave Chart.yaml alone" lacks_dep "$dir"
check "enabled dynamic plugins do not call helm" no_helm

# 3b. The render service opens the same plugin path from its own keys (the chart enables the gate when either
# block is true), so a values file that sets only those must be refused too, naming the key.
for gate_key in enabled acknowledgeArbitraryCodeExecution; do
    dir="$(new_chart "render-gate-${gate_key}")"
    printf 'renderService:\n  unsafeDynamicTemplatePlugins:\n    %s: true\n' "$gate_key" >> "$dir/values.yaml"
    if run "$dir" "${TRUSTED[@]}" "${OPT_IN[@]}"; then fail "renderService ${gate_key} true is refused"; else pass "renderService ${gate_key} true is refused"; fi
    check "the refusal names renderService.unsafeDynamicTemplatePlugins.${gate_key}" grep -q "renderService.unsafeDynamicTemplatePlugins.${gate_key}" "$work/out.log"
    check "renderService ${gate_key} leaves Chart.yaml alone" lacks_dep "$dir"
    check "renderService ${gate_key} does not call helm" no_helm
done

# 4. Opted in without a version: refuse.
dir="$(new_chart no-version)"
if run "$dir" "${TRUSTED[@]}" NVCM_TEMPLATE_PREVIEW_CHART_REPOSITORY=https://charts.example/internal; then fail "a missing version is refused"; else pass "a missing version is refused"; fi

# 5. Opted in and trusted: the dependency is added and resolved with `helm dependency update`.
dir="$(new_chart trusted)"
if run "$dir" "${TRUSTED[@]}" "${OPT_IN[@]}" NVCM_TEMPLATE_PREVIEW_CHART_TOKEN_VAR=TP_TOKEN TP_TOKEN=s3cret-value; then pass "a trusted opted-in job succeeds"; else fail "a trusted opted-in job succeeds"; cat "$work/out.log"; fi
check "the dependency is in Chart.yaml" has_dep "$dir"
dep() { yq -r ".dependencies[] | select(.name == \"nvcm-template-preview\") | .$1" "$dir/Chart.yaml"; }
check "it has the given version" test "$(dep version)" = "0.7.0"
check "it has the given repository" test "$(dep repository)" = "https://charts.example/internal"
check "it is installed as templatePreview" test "$(dep alias)" = "templatePreview"
check "it is off unless templatePreview.enabled" test "$(dep condition)" = "templatePreview.enabled"
check "the other dependencies are kept" has_named "$dir" tempo
check "dependencies are updated, not built from a stale lock" helm_called "dependency update $dir"
check "the repository is added" helm_called "repo add"
check "the token goes in through stdin" grep -q "s3cret-value" "$HELM_STDIN"
check "the token is not in a helm argument" bash -c "! grep -q s3cret-value $HELM_LOG"
check "the token is not printed" bash -c "! grep -q s3cret-value $work/out.log"

# 6. A second run does not duplicate the entry.
run "$dir" "${TRUSTED[@]}" "${OPT_IN[@]}" NVCM_TEMPLATE_PREVIEW_CHART_TOKEN_VAR=TP_TOKEN TP_TOKEN=s3cret-value || true
check "running twice leaves one dependency" test "$(yq '[.dependencies[] | select(.name == "nvcm-template-preview")] | length' "$dir/Chart.yaml")" = "1"

# 7. A local path (development) needs no repository login.
dir="$(new_chart local)"
run "$dir" NVCM_TEMPLATE_PREVIEW_CHART_REPOSITORY=file://../template-preview NVCM_TEMPLATE_PREVIEW_CHART_VERSION=0.7.0 || true
check "a file:// repository outside CI is added" has_dep "$dir"
check "a file:// repository needs no helm repo add" bash -c "! grep -q 'repo add' $HELM_LOG"

if ((failures > 0)); then echo; echo "${failures} failed"; exit 1; fi
echo; echo "all passed"
