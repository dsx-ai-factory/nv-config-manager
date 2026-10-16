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

from __future__ import annotations

import logging

from temporalio import activity
from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.activities.ib_guid_discovery.helpers import (
    build_switch_neighbor_index,
    compute_guid_mappings,
)
from nv_config_manager_workflows.activities.ib_guid_discovery.models import (
    DiscoverIBPortGuidsInput,
    DiscoverIBPortGuidsOutput,
    SyncIBGuidInput,
    SyncIBGuidOutput,
)
from nv_config_manager_workflows.runtime import get_dcim_client, get_ufm_client

log = logging.getLogger(__name__)


@activity.defn
async def discover_ib_port_guids(
    input: DiscoverIBPortGuidsInput,
) -> DiscoverIBPortGuidsOutput:
    """Discover UFM-reported port GUIDs and map them to Nautobot interfaces."""

    async with get_ufm_client(input.ufm_host, input.site) as ufm:
        ib_ports = await ufm.get_ports(unhealthy_only=False)

    if not input.switch_device_ids:
        raise ApplicationError(
            "No switch_device_ids provided, cannot resolve topology.",
            non_retryable=True,
        )

    client = get_dcim_client()
    async with client:
        topology = await client.get_ib_switch_topology(input.switch_device_ids)
        switch_id_to_name = dict(topology.switch_names)
        neighbors_by_switch_id = {
            switch_id: {
                interface_name: {
                    "device_name": neighbor.device_name,
                    "name": neighbor.interface_name,
                }
                for interface_name, neighbor in neighbors.items()
            }
            for switch_id, neighbors in topology.intended_neighbors.items()
        }

        neighbor_index = build_switch_neighbor_index(switch_id_to_name, neighbors_by_switch_id)
        dev_iface_pairs: set[tuple[str, str]] = {
            (entry["device_name"], entry["interface_name"]) for entry in neighbor_index.values()
        }
        interfaces = await client.get_ib_interface_guids(dev_iface_pairs)
        nautobot_interface_by_dev_iface = {
            (interface.device_name.lower(), interface.interface_name): {
                "id": interface.interface_id,
                "ib_guid": interface.guid,
            }
            for interface in interfaces
        }

    mappings = compute_guid_mappings(
        ib_ports=ib_ports,
        switch_id_to_name=switch_id_to_name,
        neighbors_by_switch_id=neighbors_by_switch_id,
        nautobot_interface_by_dev_iface=nautobot_interface_by_dev_iface,
    )

    counts = {"set": 0, "update": 0, "noop": 0, "skip": 0}
    for m in mappings:
        counts[m.action] = counts.get(m.action, 0) + 1
    log.info(
        "IB GUID discovery: %d mappings (set=%d, update=%d, noop=%d, skip=%d)",
        len(mappings),
        counts["set"],
        counts["update"],
        counts["noop"],
        counts["skip"],
    )

    return DiscoverIBPortGuidsOutput(
        mappings=mappings,
        display=(
            f"Discovered {len(mappings)} IB port/interface mappings "
            f"(set={counts['set']}, update={counts['update']}, "
            f"noop={counts['noop']}, skip={counts['skip']})"
        ),
    )


@activity.defn
async def sync_ib_guid_on_interface(input: SyncIBGuidInput) -> SyncIBGuidOutput:
    """Sync the `ib_guid` custom field on one Nautobot interface."""

    if not input.interface_id:
        raise ApplicationError("interface_id is required", non_retryable=True)
    if not input.guid:
        raise ApplicationError("guid is required", non_retryable=True)

    client = get_dcim_client()
    async with client:
        current = await client.get_ib_interface_guid(input.interface_id)
        previous_guid = current.guid
        device_name = current.device_name
        interface_name = current.interface_name

        if previous_guid.lower() == input.guid.lower():
            return SyncIBGuidOutput(
                interface_id=input.interface_id,
                device_name=device_name,
                interface_name=interface_name,
                previous_guid=previous_guid,
                new_guid=previous_guid,
                changed=False,
                dry_run=input.dry_run,
                reason="ib_guid already up to date",
            )

        if input.dry_run:
            return SyncIBGuidOutput(
                interface_id=input.interface_id,
                device_name=device_name,
                interface_name=interface_name,
                previous_guid=previous_guid,
                new_guid=input.guid,
                changed=False,
                dry_run=True,
                reason="dry_run=True; no write performed",
            )

        await client.set_ib_interface_guid(input.interface_id, input.guid)
        log.info(
            "Synced ib_guid on interface %s (%s/%s): '%s' -> '%s'",
            input.interface_id,
            device_name,
            interface_name,
            previous_guid,
            input.guid,
        )

        return SyncIBGuidOutput(
            interface_id=input.interface_id,
            device_name=device_name,
            interface_name=interface_name,
            previous_guid=previous_guid,
            new_guid=input.guid,
            changed=True,
            dry_run=False,
        )
