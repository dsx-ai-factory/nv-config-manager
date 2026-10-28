#!/bin/sh
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

set -eu

script_directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repository_root=$(dirname "$script_directory")
canonical_directory="$repository_root/packages/workflows/tests/fixtures/workflow_summaries"
browser_directory="$repository_root/ui/src/mocks/data/workflows/fixtures"

if [ ! -d "$canonical_directory" ]; then
    echo "Canonical workflow fixtures not found: $canonical_directory" >&2
    echo "Run this command from a full nv-config-manager repository checkout." >&2
    exit 1
fi

set -- "$canonical_directory"/*.json
if [ ! -f "$1" ]; then
    echo "No canonical workflow fixtures found in $canonical_directory" >&2
    exit 1
fi

mkdir -p "$browser_directory"
for fixture in "$canonical_directory"/*.json; do
    fixture_name=$(basename "$fixture")
    destination="$browser_directory/$fixture_name"

    if [ -f "$destination" ] && cmp -s "$fixture" "$destination"; then
        continue
    fi

    temporary_file=$(mktemp "$browser_directory/.workflow-fixture.XXXXXX")
    if ! cp "$fixture" "$temporary_file"; then
        rm -f "$temporary_file"
        exit 1
    fi
    if ! mv -f "$temporary_file" "$destination"; then
        rm -f "$temporary_file"
        exit 1
    fi
done

for fixture in "$browser_directory"/*.json; do
    [ -f "$fixture" ] || continue
    if [ ! -f "$canonical_directory/$(basename "$fixture")" ]; then
        rm -f "$fixture"
    fi
done
