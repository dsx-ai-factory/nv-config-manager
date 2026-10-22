#!/usr/bin/env bash
set -euo pipefail

chart_file="${1:?usage: publish_chart_targets.sh CHART_FILE VERSION CHART_NAME}"
chart_version="${2:?usage: publish_chart_targets.sh CHART_FILE VERSION CHART_NAME}"
chart_name="${3:?usage: publish_chart_targets.sh CHART_FILE VERSION CHART_NAME}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
chart_workspace=""
trap 'if [[ -n "$chart_workspace" ]]; then rm -rf "$chart_workspace"; fi' EXIT

trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
  return 0
}

parse_options() {
  local options="$1"
  local -n org_ref="$2"
  local -n registry_ref="$3"
  local -n username_ref="$4"
  local -n label_ref="$5"
  local -n image_target_ref="$6"

  if [[ -z "$options" ]]; then
    return 0
  fi

  local raw_option option key value
  IFS=',' read -r -a option_items <<< "$options"
  for raw_option in "${option_items[@]}"; do
    option="$(trim "$raw_option")"
    [[ -z "$option" ]] && continue
    key="$(trim "${option%%=*}")"
    value="$(trim "${option#*=}")"
    case "$key" in
      label) label_ref="$value" ;;
      org) org_ref="$value" ;;
      registry) registry_ref="$value" ;;
      username) username_ref="$value" ;;
      image_target) image_target_ref="$value" ;;
      *)
        echo "Unsupported chart target option '${key}'." >&2
        exit 1
        ;;
    esac
  done
  return 0
}

publish_target() {
  local target_type="$1"
  local repository="$2"
  local token_var="$3"
  local options="${4:-}"
  local org=""
  local registry=""
  local username=""
  local label=""
  local image_target=""
  local target_chart_file="$chart_file"

  target_type="$(trim "$target_type")"
  target_type="${target_type,,}"
  repository="$(trim "$repository")"
  token_var="$(trim "$token_var")"
  options="$(trim "$options")"

  if [[ -z "$target_type" || -z "$repository" || -z "$token_var" ]]; then
    echo "Invalid chart publish target. Expected type|repository|token_var|options." >&2
    exit 1
  fi

  parse_options "$options" org registry username label image_target
  label="${label:-$target_type:$repository}"

  local token
  token="$(printenv "$token_var" || true)"
  if [[ -z "$token" ]]; then
    echo "Chart publish target ${label} references ${token_var}, but that variable is empty or unset."
    exit 1
  fi

  if [[ -n "$image_target" ]]; then
    if [[ -z "${NVCM_IMAGE_TARGETS:-}" ]]; then
      echo "Per-chart image_target requires NVCM_IMAGE_TARGETS to be configured." >&2
      exit 1
    fi
    local image_target_exports target_dir
    image_target_exports="$(bash "$script_dir/image_target_env.sh" "$image_target")"
    eval "$image_target_exports"
    if [[ -z "$chart_workspace" ]]; then
      chart_workspace="$(mktemp -d)"
    fi
    target_dir="$(mktemp -d "${chart_workspace}/target.XXXXXX")"
    tar -xzf "$chart_file" -C "$target_dir"
    bash "$script_dir/update_helm_image_values.sh" "$target_dir/$chart_name/values.yaml" \
      "$NVCM_IMAGE_REPOSITORY" "$chart_version"
    helm package "$target_dir/$chart_name" --version "$chart_version" --app-version "$chart_version" \
      --destination "$target_dir"
    target_chart_file="$target_dir/${chart_name}-${chart_version}.tgz"
  fi

  case "$target_type" in
    ngc)
      if [[ ! -x ngc-cli/ngc ]]; then
        echo "Chart publish target ${label} requires ngc-cli/ngc to be installed."
        exit 1
      fi

      mkdir -p ~/.ngc
      {
        echo "[CURRENT]"
        echo "apikey = ${token}"
        echo "format_type = json"
        if [[ -n "$org" ]]; then
          echo "org = ${org}"
        fi
      } > ~/.ngc/config

      echo "Publishing chart to ${label}..."
      set +e
      publish_output=$(ngc-cli/ngc registry chart push --source "$target_chart_file" "${repository}/${chart_name}:${chart_version}" 2>&1)
      publish_exit=$?
      set -e

      if grep -q "already exists" <<< "$publish_output"; then
        echo "Chart already exists in ${label}; skipping."
      elif ((publish_exit != 0)); then
        echo "Chart publish to ${label} failed with exit ${publish_exit}:"
        echo "$publish_output"
        exit 1
      else
        publish_status=$(yq -r '.version.status' <<< "$publish_output" 2>/dev/null || true)
        if [[ "$publish_status" != "UPLOAD_COMPLETE" ]]; then
          echo "Chart publish to ${label} did not report UPLOAD_COMPLETE (got: ${publish_status:-<no valid JSON>}):"
          echo "$publish_output"
          exit 1
        fi
        echo "Published chart to ${label}."
      fi
      ;;
    oci)
      if [[ -z "$registry" ]]; then
        registry="${repository%%/*}"
      fi
      username="${username:-\$oauthtoken}"

      echo "Publishing chart to ${label}..."
      helm registry login "$registry" --username "$username" --password-stdin <<< "$token"
      helm push "$target_chart_file" "oci://${repository}"
      echo "Published chart to ${label}."
      ;;
    *)
      echo "Unsupported chart publish target type '${target_type}' for ${label}."
      exit 1
      ;;
  esac
  return 0
}

publish_records() {
  local records="${NVCM_CHART_TARGETS:-}"
  local count=0
  local raw_line line target_type repository token_var options

  while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
    line="$(trim "$raw_line")"
    [[ -z "$line" || "${line:0:1}" == "#" ]] && continue
    IFS='|' read -r target_type repository token_var options <<< "$line"
    publish_target "${target_type:-}" "${repository:-}" "${token_var:-}" "${options:-}"
    count=$((count + 1))
  done <<< "$records"

  if ((count == 0)); then
    echo "No external chart publish targets configured."
  fi
  return 0
}

read_lines() {
  local variable_name="$1"
  local -n output="$2"

  output=()
  if [[ -z "${!variable_name:-}" ]]; then
    return 0
  fi

  mapfile -t output <<< "${!variable_name}"
  while ((${#output[@]} > 0)); do
    local last_index
    last_index=$((${#output[@]} - 1))
    if [[ -n "${output[$last_index]}" ]]; then
      break
    fi
    unset "output[$last_index]"
  done
  return 0
}

publish_legacy_lists() {
  local types=()
  local repositories=()
  local token_vars=()
  local registries=()
  local usernames=()
  local labels=()
  local orgs=()
  local image_targets=()

  read_lines NVCM_CHART_PUBLISH_TYPES types
  read_lines NVCM_CHART_PUBLISH_REPOSITORIES repositories
  read_lines NVCM_CHART_PUBLISH_TOKEN_VARS token_vars
  read_lines NVCM_CHART_PUBLISH_REGISTRIES registries
  read_lines NVCM_CHART_PUBLISH_USERNAMES usernames
  read_lines NVCM_CHART_PUBLISH_LABELS labels
  read_lines NVCM_CHART_PUBLISH_ORGS orgs
  read_lines NVCM_CHART_PUBLISH_IMAGE_TARGETS image_targets

  local target_count="${#types[@]}"
  if ((target_count == 0)); then
    echo "No external chart publish targets configured."
    return 0
  fi

  if ((${#repositories[@]} != target_count)); then
    echo "Chart publish variable NVCM_CHART_PUBLISH_REPOSITORIES has ${#repositories[@]} entries; expected ${target_count}."
    exit 1
  fi
  if ((${#token_vars[@]} != target_count)); then
    echo "Chart publish variable NVCM_CHART_PUBLISH_TOKEN_VARS has ${#token_vars[@]} entries; expected ${target_count}."
    exit 1
  fi

  local index options
  for index in "${!types[@]}"; do
    options=""
    if ((index < ${#labels[@]})) && [[ -n "${labels[$index]}" ]]; then
      options="${options:+$options,}label=${labels[$index]}"
    fi
    if ((index < ${#orgs[@]})) && [[ -n "${orgs[$index]}" ]]; then
      options="${options:+$options,}org=${orgs[$index]}"
    fi
    if ((index < ${#registries[@]})) && [[ -n "${registries[$index]}" ]]; then
      options="${options:+$options,}registry=${registries[$index]}"
    fi
    if ((index < ${#usernames[@]})) && [[ -n "${usernames[$index]}" ]]; then
      options="${options:+$options,}username=${usernames[$index]}"
    fi
    if ((index < ${#image_targets[@]})) && [[ -n "${image_targets[$index]}" ]]; then
      options="${options:+$options,}image_target=${image_targets[$index]}"
    fi
    publish_target "${types[$index]}" "${repositories[$index]}" "${token_vars[$index]}" "$options"
  done
  return 0
}

if [[ -n "${NVCM_CHART_TARGETS:-}" ]]; then
  publish_records
else
  publish_legacy_lists
fi
