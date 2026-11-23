#!/usr/bin/env bash
# Build the chart's dependencies, adding the private Template Preview chart only in trusted internal jobs.
#
# The public Chart.yaml does not name Template Preview: its chart lives in a private registry, and a public
# repository must not point at one. Trusted internal jobs (protected branch or tag pipelines) opt in with
# the variables below, and this script adds the dependency to Chart.yaml before the chart is packaged.
# Without them it is the same as `helm dependency build`, which is what forks, the public pipeline and
# merge request builds get.
#
#   usage: build_chart_dependencies.sh CHART_DIR
#
#   NVCM_TEMPLATE_PREVIEW_CHART_REPOSITORY   helm repository holding the chart (https://, or file:// for development); unset = do not add it
#   NVCM_TEMPLATE_PREVIEW_CHART_VERSION      chart version to depend on (required when the repository is set)
#   NVCM_TEMPLATE_PREVIEW_CHART_TOKEN_VAR    optional: NAME of the variable holding the repository password (never the token itself)
#   NVCM_TEMPLATE_PREVIEW_CHART_USERNAME     optional: repository user (default $oauthtoken)
#
# The dependency is added as templatePreview and stays off unless templatePreview.enabled, and the chart
# shipped from here must not enable the dynamic plugin gate: the internal deployments run with it off.
set -euo pipefail

chart_dir="${1:?usage: build_chart_dependencies.sh CHART_DIR}"
repository="${NVCM_TEMPLATE_PREVIEW_CHART_REPOSITORY:-}"

if [[ -z "$repository" ]]; then
  helm dependency build "$chart_dir"
  exit 0
fi

die() { echo "build_chart_dependencies: $*" >&2; exit 1; }

version="${NVCM_TEMPLATE_PREVIEW_CHART_VERSION:-}"
[[ -n "$version" ]] || die "NVCM_TEMPLATE_PREVIEW_CHART_VERSION is required when NVCM_TEMPLATE_PREVIEW_CHART_REPOSITORY is set."

# Only a trusted pipeline may pull a private chart into a chart that is then published or deployed.
if [[ "${GITLAB_CI:-}" == true ]]; then
  [[ "${CI_COMMIT_REF_PROTECTED:-false}" == true ]] || die "refusing to add the Template Preview chart: this ref is not protected."
  [[ "${CI_PIPELINE_SOURCE:-}" != merge_request_event ]] || die "refusing to add the Template Preview chart in a merge request pipeline."
fi

# Internal deployments never run with user-supplied template plugins: the shipped values must keep the gate off.
# The chart opens it from either block (templatePreview, or the render service's own keys), so both are checked.
values_file="${chart_dir}/values.yaml"
[[ -f "$values_file" ]] || die "${values_file} not found."
for block in templatePreview renderService; do
  for key in enabled acknowledgeArbitraryCodeExecution; do
    value="$(yq -r ".${block}.unsafeDynamicTemplatePlugins.${key} // false" "$values_file")"
    [[ "$value" != true ]] || die "${block}.unsafeDynamicTemplatePlugins.${key} is true in ${values_file}; the chart built here must ship with it off."
  done
done

chart_file="${chart_dir}/Chart.yaml"
export TP_REPOSITORY="$repository" TP_VERSION="$version"
yq -i '
  .dependencies = ((.dependencies // []) | map(select(.name != "nvcm-template-preview"))) |
  .dependencies += [{
    "name": "nvcm-template-preview",
    "version": strenv(TP_VERSION),
    "repository": strenv(TP_REPOSITORY),
    "alias": "templatePreview",
    "condition": "templatePreview.enabled"
  }]
' "$chart_file"

if [[ "$repository" != file://* ]]; then
  repo_args=(repo add nvcm-template-preview "$repository" --force-update)
  token_var="${NVCM_TEMPLATE_PREVIEW_CHART_TOKEN_VAR:-}"
  if [[ -n "$token_var" ]]; then
    token="$(printenv "$token_var" || true)"
    [[ -n "$token" ]] || die "${token_var} is empty or unset."
    # The password goes through stdin, not the command line.
    printf '%s' "$token" | helm "${repo_args[@]}" --username "${NVCM_TEMPLATE_PREVIEW_CHART_USERNAME:-\$oauthtoken}" --password-stdin > /dev/null
  else
    helm "${repo_args[@]}" > /dev/null
  fi
fi

# The checked-in Chart.lock does not list the added chart, so resolve and re-lock instead of `build`.
helm dependency update "$chart_dir"
