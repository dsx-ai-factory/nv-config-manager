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
"""Configuration-related activities and utilities for Temporal workflows."""

from temporalio import activity

from nv_config_manager_workflows.runtime import get_ui_base_url as runtime_ui_base_url


def build_workflow_url(ui_base_url: str, workflow_id: str) -> str:
    """Build a full UI URL for a workflow, handling scheme and trailing slashes."""
    base = ui_base_url.rstrip("/")
    if base.startswith("https://") or base.startswith("http://"):
        return f"{base}/workflows/{workflow_id}"
    return f"https://{base}/workflows/{workflow_id}"


@activity.defn
def get_ui_base_url() -> str:
    """Return the NVCM UI base URL configured at process startup."""
    return runtime_ui_base_url()
