#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

set -euo pipefail

readonly GENERATOR_VERSION="7.21.0"
readonly GENERATOR_DIGEST="sha256:ce308310f3c1f8761e65338b8ab87b651bf4862c6acb80de510f381fffc4510b"
readonly GENERATOR_IMAGE="${OPENAPI_GENERATOR_IMAGE:-openapitools/openapi-generator-cli:v${GENERATOR_VERSION}@${GENERATOR_DIGEST}}"
readonly GO_VERSION="1.25"

repo_root=$(git rev-parse --show-toplevel)
output_root="$repo_root/bindings/go"
staging_root=$(mktemp -d "${TMPDIR:-/tmp}/nvcm-go-bindings.XXXXXX")
trap 'rm -rf -- "$staging_root"' EXIT
template_root="$staging_root/templates"

docker run --rm \
    --user "$(id -u):$(id -g)" \
    --volume "$staging_root:/out" \
    "$GENERATOR_IMAGE" author template \
    --generator-name go \
    --output /out/templates
patch --directory "$template_root" --strip 1 < "$repo_root/scripts/openapi-generator-go.patch"

services=(config-store dhcp render temporal ztp)

for service in "${services[@]}"; do
    repo_id="nv-config-manager/bindings/go/${service}"
    package_name="${service//-/}"
    generator_spec="$staging_root/${service}.openapi.json"

    # OpenAPI Generator drops property descriptions while simplifying nullable
    # OpenAPI 3.1 anyOf schemas. Copy the description to each non-null branch so
    # generated Go struct fields retain the schema documentation.
    jq '
        walk(
            if type == "object"
                and (.description? | type == "string")
                and (.anyOf? | type == "array")
            then
                .description as $description
                | .anyOf |= map(
                    if .type? != "null" and .description? == null
                    then . + {description: $description}
                    else .
                    end
                )
            else .
            end
        )
    ' "$repo_root/docs/api-specs/${service}.openapi.json" > "$generator_spec"

    docker run --rm \
        --user "$(id -u):$(id -g)" \
        --volume "$repo_root:/repo:ro" \
        --volume "$staging_root:/out" \
        "$GENERATOR_IMAGE" generate \
        --input-spec "/out/${service}.openapi.json" \
        --generator-name go \
        --template-dir /out/templates \
        --output "/out/${service}" \
        --git-host github.com \
        --git-user-id nvidia \
        --git-repo-id "$repo_id" \
        --additional-properties "packageName=${package_name},packageVersion=0.0.0,goVersion=${GO_VERSION},withGoMod=false,hideGenerationTimestamp=true,disallowAdditionalPropertiesIfNotPresent=false" \
        --global-property apiDocs=false,apiTests=false,modelDocs=false,modelTests=false

    # Retain only the generated Go client. Markdown, copied specs, push helpers, and generator
    # bookkeeping duplicate repository-owned sources or describe standalone repositories.
    rm -rf \
        "$staging_root/$service/.gitignore" \
        "$staging_root/$service/.openapi-generator" \
        "$staging_root/$service/.openapi-generator-ignore" \
        "$staging_root/$service/.travis.yml" \
        "$staging_root/$service/README.md" \
        "$staging_root/$service/api" \
        "$staging_root/$service/docs" \
        "$staging_root/$service/git_push.sh"
done

mkdir -p "$output_root"
for service in "${services[@]}"; do
    mkdir -p "$output_root/$service"
    rsync --archive --delete "$staging_root/$service/" "$output_root/$service/"
done

go -C "$output_root" mod tidy
go -C "$output_root" fmt ./...

printf 'Generated Go bindings with OpenAPI Generator %s.\n' "$GENERATOR_VERSION"
