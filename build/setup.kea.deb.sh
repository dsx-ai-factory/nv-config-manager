#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
#
# Configure the Kea 2.6 repository without executing a downloaded setup script.
set -euo pipefail

. /etc/os-release

key_file=$(mktemp)
trap 'rm -f "$key_file"' EXIT
curl -fsSL 'https://dl.cloudsmith.io/public/isc/kea-2-6/gpg.63D408891D8B8D01.key' -o "$key_file"
echo "0e14bebffdec7a47d6d265abf42766369b0f5dd21a9038b9b3ec1c964940837b  $key_file" | sha256sum -c -
gpg --batch --yes --dearmor -o /usr/share/keyrings/isc-kea-2-6-archive-keyring.gpg "$key_file"
chmod 644 /usr/share/keyrings/isc-kea-2-6-archive-keyring.gpg
printf 'deb [signed-by=/usr/share/keyrings/isc-kea-2-6-archive-keyring.gpg] https://dl.cloudsmith.io/public/isc/kea-2-6/deb/%s %s main\n' \
    "$ID" "$VERSION_CODENAME" > /etc/apt/sources.list.d/isc-kea-2-6.list
