#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

version="${1:?usage: publish_image_targets.sh VERSION [--latest]}"
publish_latest=false
case "${2:-}" in
    '') ;;
    --latest)
        if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            echo "Only stable releases can update latest." >&2
            exit 1
        fi
        # The CI job fetches tags after acquiring its shared resource lock.
        newest_stable="$(git tag --list | awk '/^[0-9]+\.[0-9]+\.[0-9]+$/' | sort -V | tail -n 1)"
        if [[ -z "$newest_stable" ]]; then
            echo "No stable release tags found; refusing to update latest." >&2
            exit 1
        fi
        if [[ "$version" != "$newest_stable" ]]; then
            echo "Skipping latest for ${version}; newest stable tag is ${newest_stable}."
            exit 0
        fi
        publish_latest=true
        ;;
    *) echo "Unsupported image publishing mode." >&2; exit 1 ;;
esac
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

image_target_exports="$(bash "$script_dir/image_target_env.sh")"
eval "$image_target_exports"
source_repository="$NVCM_IMAGE_REPOSITORY"
source_registry="$NVCM_IMAGE_REGISTRY"
source_username="$NVCM_IMAGE_USERNAME"
source_token_var="$NVCM_IMAGE_TOKEN_VAR"

# Resolve every destination before publishing anything. Each registry may need
# a different service key, even when the destinations share the same hostname.
repositories=()
registries=()
usernames=()
token_vars=()
names=()
if [[ "$publish_latest" == true ]]; then
    repositories+=("$source_repository")
    registries+=("$source_registry")
    usernames+=("$source_username")
    token_vars+=("$source_token_var")
fi
while IFS= read -r record || [[ -n "$record" ]]; do
    record="${record#"${record%%[![:space:]]*}"}"
    [[ -z "$record" || "${record:0:1}" == '#' ]] && continue
    name="${record%%|*}"
    name="${name%"${name##*[![:space:]]}"}"
    if [[ -z "$name" || "$record" != *'|'* ]]; then
        echo "Invalid image publish target. Expected name|repository|token_variable|options." >&2
        exit 1
    fi
    for previous_name in "${names[@]}"; do
        if [[ "$name" == "$previous_name" ]]; then
            echo "Duplicate image target name: ${name}." >&2
            exit 1
        fi
    done
    names+=("$name")
    image_target_exports="$(bash "$script_dir/image_target_env.sh" "$name")"
    eval "$image_target_exports"
    [[ "$NVCM_IMAGE_REPOSITORY" == "$source_repository" ]] && continue
    repositories+=("$NVCM_IMAGE_REPOSITORY")
    registries+=("$NVCM_IMAGE_REGISTRY")
    usernames+=("$NVCM_IMAGE_USERNAME")
    token_vars+=("$NVCM_IMAGE_TOKEN_VAR")
done <<< "${NVCM_IMAGE_TARGETS:-}"

if ((${#repositories[@]} == 0)); then
    echo "No additional image publish targets configured."
    exit 0
fi

for token_var in "$source_token_var" "${token_vars[@]}"; do
    if [[ ! "$token_var" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]]; then
        echo "Invalid image publish token variable name." >&2
        exit 1
    fi
    if [[ -z "${!token_var:-}" ]]; then
        echo "Image publish target references ${token_var}, but it is empty or unset." >&2
        exit 1
    fi
done

auth_dir="$(mktemp -d)"
trap 'rm -rf "$auth_dir"' EXIT
umask 077
printf '%s' "${!source_token_var}" | skopeo login --authfile "$auth_dir/source.json" \
    --username "$source_username" --password-stdin "$source_registry"
for index in "${!repositories[@]}"; do
    token_var="${token_vars[$index]}"
    printf '%s' "${!token_var}" | skopeo login --authfile "$auth_dir/target-${index}.json" \
        --username "${usernames[$index]}" --password-stdin "${registries[$index]}"
done

images=(nv-config-manager nv-config-manager-ui nv-config-manager-kea
    nv-config-manager-kea-admin nv-config-manager-nautobot nv-config-manager-nats-ready
    nv-config-manager-temporal nv-config-manager-temporal-bootstrap nv-config-manager-temporal-ui)
digests=()
for image in "${images[@]}"; do
    digest="$(skopeo inspect --authfile "$auth_dir/source.json" --format '{{.Digest}}' \
        "docker://${source_repository}/${image}:${version}")"
    if [[ ! "$digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
        echo "Invalid source digest for ${image}:${version}." >&2
        exit 1
    fi
    digests+=("$digest")
    for index in "${!repositories[@]}"; do
        if [[ "$publish_latest" == true ]]; then
            published_digest="$(skopeo inspect --authfile "$auth_dir/target-${index}.json" --format '{{.Digest}}' \
                "docker://${repositories[$index]}/${image}:${version}")"
            if [[ "$published_digest" != "$digest" ]]; then
                echo "Release digest does not match at ${repositories[$index]}/${image}:${version}." >&2
                exit 1
            fi
            continue
        fi
        echo "Publishing ${image}:${version} to ${repositories[$index]}..."
        skopeo copy --all --preserve-digests --retry-times 3 \
            --src-authfile "$auth_dir/source.json" --dest-authfile "$auth_dir/target-${index}.json" \
            --digestfile "$auth_dir/digest" \
            "docker://${source_repository}/${image}@${digest}" \
            "docker://${repositories[$index]}/${image}:${version}"
        if [[ "$(cat "$auth_dir/digest")" != "$digest" ]]; then
            echo "Published digest does not match the source for ${image}:${version}." >&2
            exit 1
        fi
    done
done

# Every versioned image in every destination was checked above. No mutable tag
# is written during versioned copies or before this complete preflight passes.
if [[ "$publish_latest" == true ]]; then
    for image_index in "${!images[@]}"; do
        image="${images[$image_index]}"
        digest="${digests[$image_index]}"
        for index in "${!repositories[@]}"; do
            skopeo copy --all --preserve-digests --retry-times 3 \
                --src-authfile "$auth_dir/source.json" --dest-authfile "$auth_dir/target-${index}.json" \
                --digestfile "$auth_dir/digest" \
                "docker://${source_repository}/${image}@${digest}" \
                "docker://${repositories[$index]}/${image}:latest"
            if [[ "$(cat "$auth_dir/digest")" != "$digest" ]]; then
                echo "Latest digest does not match at ${repositories[$index]}/${image}." >&2
                exit 1
            fi
        done
    done
fi
