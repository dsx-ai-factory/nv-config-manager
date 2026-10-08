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

# Prove the workflow package and the fixture plugin work from built artifacts
# without the root nv-config-manager distribution: build the wheels, install them
# into an empty virtual environment, and run workflow_wheels_probe.py from a
# directory outside the repository with an isolated interpreter.
#
# Needs network access for PyPI (build backends and third-party wheels) and, on
# first use, the Temporal dev server download. KEEP_WORKDIR=1 keeps the work dir.

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
work=$(mktemp -d "${TMPDIR:-/tmp}/nvcm-workflow-wheels.XXXXXX")
if [[ "${KEEP_WORKDIR:-}" == 1 ]]; then
  trap 'echo "Kept work dir $work"' EXIT
else
  trap 'rm -rf -- "$work"' EXIT
fi
dist="$work/dist"
venv_python="$work/venv/bin/python"

cd "$repo_root"

echo "==> Building wheels and the workflows sdist into $dist"
for package in logging clients infrastructure dcim; do
  uv build --package "nv-config-manager-$package" --wheel --out-dir "$dist"
done
# Without --wheel/--sdist, uv builds the sdist and then the wheel from it.
uv build --package nv-config-manager-workflows --out-dir "$dist"
uv build packages/workflows/tests/fixtures/plugin --wheel --out-dir "$dist"

echo "==> Exporting the locked third-party requirements"
uv export --package nv-config-manager-workflows --frozen --no-dev --no-emit-workspace \
  --no-header --output-file "$work/requirements.txt" >/dev/null

echo "==> Installing the built wheels into an empty virtual environment"
uv venv --python "$(<.python-version)" "$work/venv"
# Install the hash-pinned third-party requirements first, then our wheels with
# --no-index: a missing nv-config-manager-* wheel then fails the install instead
# of being looked up by name on an index (dependency confusion).
uv pip install --python "$venv_python" -r "$work/requirements.txt"
uv pip install --python "$venv_python" --no-index "$dist"/*.whl

echo "==> Running the probe from $work"
cd "$work"
# Unsetting PYTHONPATH also covers the console-script child, which runs without -I.
env -u PYTHONPATH "$venv_python" -I -u "$repo_root/scripts/workflow_wheels_probe.py" \
  --dist "$dist"

echo "==> Workflow wheel tests passed in ${SECONDS}s"
