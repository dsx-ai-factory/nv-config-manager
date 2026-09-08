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
"""Deprecated import path for :mod:`nv_config_manager_workflows.stage`.

The stage framework moved to the standalone workflows package, split across
``stage/{models,exceptions,executor,presentation,mixin}.py``. This module
re-exports the public surface so existing service imports keep resolving to the
same class objects -- which matters because the service checks stage types with
``isinstance`` and ``except``. Import from ``nv_config_manager_workflows`` in new
code.

Note for test authors: the implementation no longer lives here, so patch targets
must name the package module, e.g.
``nv_config_manager_workflows.stage.mixin.workflow.time`` and
``nv_config_manager_workflows.stage.executor.traceback.format_exc``.
"""

from nv_config_manager_workflows.stage import (
    STAGE_STATE_SEARCH_ATTRIBUTES_PATCH,
    HistoryEntry,
    Review,
    ReviewSignalInput,
    Stage,
    StageInput,
    StageMixin,
    StageOutput,
    StageRuntimeFailure,
    StageStateFailure,
    StageWorkflowInput,
    StateEnum,
    stage_executor,
)

__all__ = [
    "STAGE_STATE_SEARCH_ATTRIBUTES_PATCH",
    "HistoryEntry",
    "Review",
    "ReviewSignalInput",
    "Stage",
    "StageInput",
    "StageMixin",
    "StageOutput",
    "StageRuntimeFailure",
    "StageStateFailure",
    "StageWorkflowInput",
    "StateEnum",
    "stage_executor",
]
