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
"""Keep reusable packages independent of services and workflow runtime code."""

import ast
from pathlib import Path

import nv_config_manager_infrastructure

import nv_config_manager_clients


def test_public_sdk_does_not_import_server_or_infrastructure() -> None:
    root = Path(nv_config_manager_clients.__file__).parent
    _assert_no_imports(
        root,
        {
            "nv_config_manager",
            "nv_config_manager_workflows",
            "nv_config_manager_infrastructure",
            "temporalio",
            "redis",
            "nats",
        },
    )


def test_infrastructure_does_not_import_consumers() -> None:
    root = Path(nv_config_manager_infrastructure.__file__).parent
    _assert_no_imports(
        root,
        {
            "nv_config_manager",
            "nv_config_manager_workflows",
            "nv_config_manager_clients",
            "temporalio",
        },
    )


def _assert_no_imports(root: Path, forbidden: set[str]) -> None:
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert name.split(".")[0] not in forbidden, (path, name)
