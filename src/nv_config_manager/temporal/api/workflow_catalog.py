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
"""Workflow registry snapshot and the workflow catalogs the Temporal API serves."""

from nv_config_manager.temporal.workflow_registry import build_workflow_registry

# Dynamic routes are constructed while the API module is imported. Build the
# registry once so every API catalog surface uses the same validated snapshot.
WORKFLOW_REGISTRY = build_workflow_registry()
# API-enabled workflows: dynamic POST routes and /metadata.
WORKFLOW_API_CATALOG = tuple(WORKFLOW_REGISTRY.api_workflows)
# Every registered workflow, including API-disabled child workflows: /types.
WORKFLOW_TYPE_CATALOG = tuple(WORKFLOW_REGISTRY.all_workflows)

__all__ = ["WORKFLOW_API_CATALOG", "WORKFLOW_REGISTRY", "WORKFLOW_TYPE_CATALOG"]
