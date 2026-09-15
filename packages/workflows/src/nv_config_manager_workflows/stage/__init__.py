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
"""The stage framework: a workflow decomposed into reviewable, retryable steps."""

from nv_config_manager_workflows.stage.exceptions import StageRuntimeFailure, StageStateFailure
from nv_config_manager_workflows.stage.executor import stage_executor
from nv_config_manager_workflows.stage.mixin import (
    STAGE_STATE_SEARCH_ATTRIBUTES_PATCH,
    StageMixin,
)
from nv_config_manager_workflows.stage.models import (
    HistoryEntry,
    Review,
    ReviewSignalInput,
    Stage,
    StageInput,
    StageOutput,
    StageWorkflowInput,
    StateEnum,
)
from nv_config_manager_workflows.stage.presentation import (
    compress_stages,
    decompress_stages,
    format_row_for_markdown_table,
    render_markdown_table,
    render_markdown_table_dict,
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
    "compress_stages",
    "decompress_stages",
    "format_row_for_markdown_table",
    "render_markdown_table",
    "render_markdown_table_dict",
    "stage_executor",
]
