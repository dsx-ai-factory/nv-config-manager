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
"""UFM port retrieval activity implementation."""

from temporalio import activity

from nv_config_manager_workflows.activities.ufm.helpers import _generate_ports_csv
from nv_config_manager_workflows.activities.ufm.models import GetUFMPortsInput, GetUFMPortsOutput
from nv_config_manager_workflows.runtime import get_ufm_client


@activity.defn
async def get_ib_ports(input: GetUFMPortsInput) -> GetUFMPortsOutput:
    """Get InfiniBand ports from UFM REST API.

    Supports password rotation with automatic retry on 401/403.

    Args:
        input: Input containing host, unhealthy flag, and optional site.
            If unhealthy is True, only returns unhealthy ports.
            If unhealthy is False, returns all ports.

    Returns:
        GetUFMPortsOutput containing list of ports and CSV data.
    """
    async with get_ufm_client(input.host, input.site) as client:
        ports = await client.get_ports(unhealthy_only=input.unhealthy)
        csv_data = _generate_ports_csv(ports)

        return GetUFMPortsOutput(
            ports=ports,
            csv_data=csv_data,
            display="UFM ports retrieved successfully.",
        )
