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
"""Temporal payload models for render service activities."""

from nv_config_manager_clients.render import FileCommit
from pydantic import BaseModel, Field


class ExecuteRenderInput(BaseModel):
    """Input for executing a render operation."""

    device_id: str
    workflow_id: str


class ExecuteRenderOutput(BaseModel):
    """Output for render operation."""

    updated_files: list[FileCommit] = Field(default_factory=list)
    snapshot_files: list[FileCommit] = Field(default_factory=list)

    def get_commit(self, filename: str) -> str | None:
        """Look up a commit ID in the post-render Config Store snapshot."""
        return next((fc.commit for fc in self.snapshot_files if fc.filename == filename), None)


__all__ = [
    "ExecuteRenderInput",
    "ExecuteRenderOutput",
]
