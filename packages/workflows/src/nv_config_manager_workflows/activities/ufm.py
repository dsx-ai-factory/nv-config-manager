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
"""Infiniband/UFM Temporal Activities."""

import csv
from io import StringIO
from typing import Any

from pydantic import BaseModel
from temporalio import activity

from nv_config_manager_workflows.runtime import get_ufm_client
from nv_config_manager_workflows.stage.models import StageOutput


class GetUFMPortsInput(BaseModel):
    """Inputs for UFM ports API."""

    host: str
    unhealthy: bool = False
    site: str | None = None


class GetUFMPortsOutput(StageOutput):
    """Output for getting UFM ports."""

    csv_data: str
    display: str
    ports: list[dict[str, Any]]


def _generate_ports_csv(ports: list[dict[str, Any]]) -> str:
    """Generate CSV string from port data.

    Args:
        ports: List of port dictionaries

    Returns:
        CSV formatted string
    """
    if not ports:
        return ""

    output = StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "system_name",
            "port",
            "label",
            "description",
            "physical_state",
            "logical_state",
            "peer_node_name",
            "peer_port",
            "peer_node_description",
            "guid",
        ],
    )
    writer.writeheader()
    writer.writerows(ports)
    return output.getvalue()


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


UFM_ACTIVITIES = (get_ib_ports,)

__all__ = [
    "GetUFMPortsInput",
    "GetUFMPortsOutput",
    "UFM_ACTIVITIES",
    "get_ib_ports",
]
