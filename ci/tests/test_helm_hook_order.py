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
"""Render hooks in dependency order for Helm and Argo CD."""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART = Path(__file__).resolve().parents[2] / "deploy" / "helm"


def test_config_store_migration_precedes_template_updater() -> None:
    if shutil.which("helm") is None or not (CHART / "charts").is_dir():
        pytest.skip("helm and vendored chart dependencies are required")
    result = subprocess.run(
        ["helm", "template", "test", str(CHART), "--values", str(CHART / "values-ci.yaml")],
        capture_output=True,
        text=True,
        check=True,
    )
    jobs = {
        doc["metadata"]["labels"]["component"]: doc["metadata"]["annotations"]
        for doc in yaml.safe_load_all(result.stdout)
        if doc
        and doc.get("kind") == "Job"
        and doc["metadata"].get("labels", {}).get("component") in {"db-init", "template-updater"}
        and (
            "config-store" in doc["metadata"]["name"]
            or doc["metadata"]["labels"]["component"] == "template-updater"
        )
    }
    migration = jobs["db-init"]
    updater = jobs["template-updater"]
    for annotations in (migration, updater):
        assert set(annotations["helm.sh/hook"].split(",")) == {"post-install", "post-upgrade"}
    assert int(migration["helm.sh/hook-weight"]) < int(updater["helm.sh/hook-weight"])
