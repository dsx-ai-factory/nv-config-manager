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
# Shared by GitLab CI and the chart's source-plugin installer.
# Keep bootstrap versions and release hashes here. Sourcing does not access the network.
NVCM_PIP_VERSION=26.2.2
NVCM_PIP_REQUIREMENT='pip @ https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/pip-26.2.2-py3-none-any.whl#sha256=fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70'
NVCM_UV_REQUIREMENT='uv==0.13.5 --hash=sha256:2386c2dc9b59fc1d39b1c500e4319d71d3268232eb3b38e29cd6775d6269e8d4 --hash=sha256:fa8d26e08e34c29c0c4e820cf3181757086d547240b4f4d31ff1d8bd810ca946 --hash=sha256:91e58e304b63b6c91bac5561459ab0c3ea24f17bc01ea9359d69fed72142978f --hash=sha256:d162669e3a7811c584b03690eaa11e796ad1d309aa8229d5dfe3cbcd68a90400'
nvcm_pip_ready=false
nvcm_source_ready=false

nvcm_upgrade_pip() {
    if [ "$nvcm_pip_ready" = true ]; then
        return 0
    fi
    # The hash protects this first download even if the old pip trusts a bad CA.
    pip install --disable-pip-version-check --no-deps "$NVCM_PIP_REQUIREMENT" || return 1
    nvcm_pip_ready=true
}

nvcm_bootstrap_pip() {
    if [ "$nvcm_source_ready" = true ]; then
        return 0
    fi
    nvcm_upgrade_pip || return 1
    # Source builds also need patched system/Requests trust for their subprocesses.
    SSL_CERT_FILE="$(python -c 'from pip._vendor import certifi; print(certifi.where())')" || return 1
    SSL_CERT_DIR="$(mktemp -d)" || return 1
    REQUESTS_CA_BUNDLE="$SSL_CERT_FILE"
    export SSL_CERT_FILE SSL_CERT_DIR REQUESTS_CA_BUNDLE
    nvcm_source_ready=true
}

nvcm_bootstrap_uv() {
    # Preserve the CI image's configured certificate settings. Both downloads are hashed.
    nvcm_upgrade_pip || return 1
    printf '%s\n' "$NVCM_UV_REQUIREMENT" | pip install --no-deps --require-hashes -r /dev/stdin
}
