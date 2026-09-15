#!/usr/bin/env bash
# Resolve a shared test environment's deployment configuration from the
# NVCM_TEST_ENV_TARGETS CI variable (GHF: env branch/namespace/path names are
# internal details, so they live in protected GitLab variables, not in this
# public file). Prints shell exports; callers eval the output, mirroring
# image_target_env.sh.
#
# Usage: eval "$(test_env_config.sh <env>)"
#
# NVCM_TEST_ENV_TARGETS holds one record per line:
#   env|env_branch|namespace|release_name|baseline_values|state_dir|argocd_application
# Example:
#   test|<env-branch>|<namespace>|<release>|<baseline-values>|<state-dir>|<argocd-application>
#
# Exports: NVCM_ENV, NVCM_ENV_BRANCH, NVCM_ENV_NAMESPACE,
#          NVCM_ENV_RELEASE_NAME, NVCM_ENV_BASELINE_VALUES,
#          NVCM_ENV_STATE_DIR, NVCM_ENV_ARGOCD_APPLICATION
set -euo pipefail

trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
  return 0
}

shell_export() {
  local name="$1"
  local value="$2"
  printf 'export %s=%q\n' "$name" "$value"
  return 0
}

requested_env="$(trim "${1:?usage: test_env_config.sh <env>}")"

# Defence in depth. The promote pipeline's rules already constrain
# NVCM_PROMOTE_ENV to the shared test environments, but this script is the point
# where an environment name becomes a concrete branch, namespace and release
# name - so refuse anything outside that set here too. A mis-set variable, a
# stray NVCM_TEST_ENV_TARGETS record, or a future caller that forgets the rule
# then fails closed instead of resolving production.
#
# Deliberately hardcoded rather than configurable: making the allowlist itself
# overridable would let the same variable injection this guards against widen
# it. Adding an environment is a reviewed code change, by design.
case "$requested_env" in
  test|test01) ;;
  *)
    echo "Refusing to resolve environment '${requested_env}'." >&2
    echo "Only the shared test environments (test, test01) may be resolved;" >&2
    echo "production is deployed by the tag-driven release flow, not this one." >&2
    exit 1
    ;;
esac

records="${NVCM_TEST_ENV_TARGETS:-}"

if [[ -z "$records" ]]; then
  echo "NVCM_TEST_ENV_TARGETS is empty or unset (protected variable; only available on protected refs)." >&2
  exit 1
fi

while IFS= read -r raw_record; do
  record="$(trim "$raw_record")"
  [[ -z "$record" || "$record" == \#* ]] && continue

  separators="${record//[!|]/}"
  if (( ${#separators} != 6 )); then
    echo "NVCM_TEST_ENV_TARGETS record must contain exactly seven fields." >&2
    exit 1
  fi
  IFS='|' read -r env env_branch namespace release_name baseline_values state_dir argocd_application <<< "$record"
  env="$(trim "$env")"
  [[ "$env" == "$requested_env" ]] || continue

  env_branch="$(trim "$env_branch")"
  namespace="$(trim "$namespace")"
  release_name="$(trim "$release_name")"
  baseline_values="$(trim "$baseline_values")"
  state_dir="$(trim "$state_dir")"
  argocd_application="$(trim "${argocd_application:-}")"
  for field in env_branch namespace release_name baseline_values state_dir argocd_application; do
    if [[ -z "${!field}" ]]; then
      echo "NVCM_TEST_ENV_TARGETS record for '${env}' is missing field '${field}'." >&2
      exit 1
    fi
  done

  shell_export NVCM_ENV "$env"
  shell_export NVCM_ENV_BRANCH "$env_branch"
  shell_export NVCM_ENV_NAMESPACE "$namespace"
  shell_export NVCM_ENV_RELEASE_NAME "$release_name"
  shell_export NVCM_ENV_BASELINE_VALUES "$baseline_values"
  shell_export NVCM_ENV_STATE_DIR "$state_dir"
  shell_export NVCM_ENV_ARGOCD_APPLICATION "$argocd_application"
  exit 0
done <<< "$records"

echo "No NVCM_TEST_ENV_TARGETS record found for environment '${requested_env}'." >&2
exit 1
