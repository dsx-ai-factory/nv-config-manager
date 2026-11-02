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
"""InfiniBand GUID discovery activities and public exports."""

from nv_config_manager_workflows.activities.ib_guid_discovery.activities import (
    discover_ib_port_guids,
    sync_ib_guid_on_interface,
)
from nv_config_manager_workflows.activities.ib_guid_discovery.helpers import (
    compute_guid_mappings as compute_guid_mappings,
)
from nv_config_manager_workflows.activities.ib_guid_discovery.models import (
    IB_GUID_CF_KEY,
    DiscoverIBPortGuidsInput,
    DiscoverIBPortGuidsOutput,
    IBGuidMapping,
    SyncIBGuidInput,
    SyncIBGuidOutput,
)

IB_GUID_DISCOVERY_ACTIVITIES = (
    discover_ib_port_guids,
    sync_ib_guid_on_interface,
)

__all__ = [
    "IB_GUID_CF_KEY",
    "IB_GUID_DISCOVERY_ACTIVITIES",
    "DiscoverIBPortGuidsInput",
    "DiscoverIBPortGuidsOutput",
    "IBGuidMapping",
    "SyncIBGuidInput",
    "SyncIBGuidOutput",
    "discover_ib_port_guids",
    "sync_ib_guid_on_interface",
]
