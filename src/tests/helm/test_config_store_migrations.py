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
"""Config-store schema migrations run before the API image serves."""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART = Path(__file__).resolve().parents[3] / "deploy/helm"
ALEMBIC_UPGRADE = ["alembic", "-c", "db/alembic.ini", "upgrade", "head"]


def render(*values_files: str) -> list[dict]:
    """Render the chart with the CI values and any extra values files."""
    if shutil.which("helm") is None or not (CHART / "charts").is_dir():
        pytest.skip("Helm and vendored chart dependencies are required")
    command = ["helm", "template", "test", str(CHART), "--values", str(CHART / "values-ci.yaml")]
    for values_file in values_files:
        command.extend(["--values", str(CHART / values_file)])
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    return [document for document in yaml.safe_load_all(result.stdout) if document]


def config_store_api(documents: list[dict]) -> dict:
    """Return the config-store API pod spec."""
    deployments = [
        document
        for document in documents
        if document.get("kind") == "Deployment"
        and document["metadata"]["name"].endswith("config-store-api")
    ]
    assert len(deployments) == 1
    return deployments[0]["spec"]["template"]["spec"]


def test_api_migrates_before_it_starts() -> None:
    """The API pod waits for Postgres, then upgrades the schema, then starts."""
    pod = config_store_api(render())
    init_names = [container["name"] for container in pod["initContainers"]]
    assert init_names.index("wait-for-postgres") < init_names.index("migrate")

    migrate = next(c for c in pod["initContainers"] if c["name"] == "migrate")
    api = next(c for c in pod["containers"] if c["name"] == "api")
    assert migrate["command"] == ALEMBIC_UPGRADE
    assert migrate["image"] == api["image"]
    env = {item["name"]: item["value"] for item in migrate["env"]}
    api_env = {item["name"]: item.get("value") for item in api["env"]}
    assert env["NV_CONFIG_MANAGER_INI"] == api_env["NV_CONFIG_MANAGER_INI"]


def test_no_post_rollout_migration_hook() -> None:
    """No hook Job runs migrations after the new API pods are already serving."""
    for document in render():
        if document.get("kind") != "Job":
            continue
        for container in document["spec"]["template"]["spec"]["containers"]:
            assert container.get("command") != ALEMBIC_UPGRADE
