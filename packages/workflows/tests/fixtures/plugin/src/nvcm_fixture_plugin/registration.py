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
"""The fixture plugin's descriptor, published through its entry point."""

from nv_config_manager_workflows.registration import WorkflowPluginDescriptor
from nvcm_fixture_plugin.activities import FIXTURE_ACTIVITIES
from nvcm_fixture_plugin.schedulers import FIXTURE_SCHEDULERS
from nvcm_fixture_plugin.workflows import FIXTURE_WORKFLOWS

PLUGIN_NAME = "nvcm-fixture"


def plugin() -> WorkflowPluginDescriptor:
    """Return the fixture plugin's descriptor."""
    return WorkflowPluginDescriptor(
        name=PLUGIN_NAME,
        workflows=FIXTURE_WORKFLOWS,
        activities=FIXTURE_ACTIVITIES,
        schedulers=FIXTURE_SCHEDULERS,
    )


__all__ = ["PLUGIN_NAME", "plugin"]
