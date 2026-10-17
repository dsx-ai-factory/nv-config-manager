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

# Run the complete workflows package suite from a non-editable installation.
# The temporary environment contains the workflows distribution, its declared
# workspace dependencies, and its own dev group, but not the root service.

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
work=$(mktemp -d "${TMPDIR:-/tmp}/nvcm-workflow-package.XXXXXX")
trap 'rm -rf -- "$work"' EXIT

coverage_file="$repo_root/packages/workflows/coverage.xml"
export UV_PROJECT_ENVIRONMENT="$work/venv"

cd "$repo_root"

echo "==> Installing the workflows package non-editably into $UV_PROJECT_ENVIRONMENT"
# uv's default cache key for local directory packages does not include every
# source file. Refresh each installed workspace distribution so this isolated
# environment always tests the current checkout rather than a cached wheel.
uv sync --frozen --package nv-config-manager-workflows --no-editable \
  --refresh-package nv-config-manager-clients \
  --refresh-package nv-config-manager-dcim \
  --refresh-package nv-config-manager-infrastructure \
  --refresh-package nv-config-manager-logging \
  --refresh-package nv-config-manager-workflows

echo "==> Verifying the root service is absent from the package environment"
env -u PYTHONPATH uv run --no-sync --package nv-config-manager-workflows \
  python -I -c '
import os
from importlib.util import find_spec
from pathlib import Path

import nv_config_manager_workflows as workflows

assert find_spec("nv_config_manager") is None
assert Path(workflows.__file__).resolve().is_relative_to(
    Path(os.environ["UV_PROJECT_ENVIRONMENT"]).resolve()
)
'

echo "==> Running the isolated workflows package suite"
# Replay and scheduler tests remain in the full suite so all package tests feed
# one coverage report without starting duplicate Temporal test environments.
env -u PYTHONPATH uv run --no-sync --package nv-config-manager-workflows \
  python -I -m pytest \
  -c "$repo_root/packages/workflows/pyproject.toml" \
  "$repo_root/packages/workflows/tests" \
  -n auto \
  --cov=nv_config_manager_workflows \
  --cov-report="xml:$coverage_file" \
  --cov-report=term-missing

# Coverage observes the installed wheel under the temporary environment. Map
# those paths to the identical repository sources before Sonar consumes them.
env -u PYTHONPATH uv run --no-sync --package nv-config-manager-workflows \
  python -I "$repo_root/scripts/workflows/normalize_coverage.py" \
  "$coverage_file" --repository "$repo_root"

echo "==> Workflows package tests passed in ${SECONDS}s"
