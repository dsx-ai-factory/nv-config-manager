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
"""Workflows related to Redfish configuration on Host and DPU BMCs."""

from __future__ import annotations

import asyncio
import copy
import logging
from datetime import timedelta

from pydantic import BaseModel, Field
from temporalio import workflow
from temporalio.common import RetryPolicy

from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.temporal.common.decorators.workflow import run_nv_config_manager_workflow
from nv_config_manager.temporal.common.mixins.metadata import WorkflowMetadataMixin
from nv_config_manager.temporal.common.mixins.stage import (
    StageInput,
    StageMixin,
    StageOutput,
    StateEnum,
    stage_executor,
)
from nv_config_manager.temporal.common.workflow_references import LocationReference

with workflow.unsafe.imports_passed_through():
    from nv_config_manager.temporal.client.redfish import RedfishHost, RedfishVendor
    from nv_config_manager.temporal.common.mixins.archive import ArchiveMixin
    from nv_config_manager.temporal.common.mixins.device import (
        HostDeviceData,
        NetworkDeviceData,
    )
    from nv_config_manager.temporal.ngc.activities.bmc import (
        DiscoverHostsInput,
        GetDpuDetailsActivityInput,
        GetServerDetailsActivityInput,
        PopulateRedfishMacsInput,
        RedfishDpu,
        RedfishHostInput,
        RedfishServer,
        UpdateDpuDataActivityInput,
        discover_redfish_hosts,
        factory_reset_bmc,
        get_dpu_details,
        get_server_details,
        populate_redfish_macs,
        power_on_host,
        set_redfish_password,
        update_dpu_data,
    )
    from nv_config_manager.temporal.ngc.activities.dcim import (
        GetNetworkDevicesInput,
        get_network_devices,
    )
    from nv_config_manager.temporal.ngc.activities.device import get_device_arp_table

ACTIVITY_NO_RETRY_POLICY = RetryPolicy(maximum_attempts=1)
DEFAULT_ACTIVITY_RETRY_POLICY = RetryPolicy(maximum_attempts=3)
DCIM_STAGE_IDENTIFIERS_PATCH = "dcim-stage-identifiers-v1"


logger = get_logger(__name__, category=LogCategory.TEMPORAL_WORKFLOW)
logger.setLevel(logging.INFO)


NIC_MANUFACTURER_MELLANOX = ["Mellanox Technologies", "MLNX"]


class RedfishProvisioningInput(BaseModel):
    """Input for Redfish provisioning workflow."""

    site: LocationReference = Field(description="Site containing the BMC network to provision.")
    bmc_switch_roles: list[str] = Field(
        description="Switch roles used to discover BMC-connected network interfaces."
    )
    ip_range_start: str = Field(description="First BMC IP address to scan.")
    ip_range_end: str = Field(description="Last BMC IP address to scan.")
    port: int = Field(default=443, description="HTTPS port used to contact Redfish services.")
    http_timeout_s: int = Field(
        default=5, description="Timeout in seconds for Redfish HTTP requests."
    )
    dpu_manufacturers: list[str] = Field(
        default=NIC_MANUFACTURER_MELLANOX,
        description="DPU manufacturer names eligible for Redfish provisioning.",
    )


class RedfishProvisioningResult(BaseModel):
    """Results for the Redfish provisioning workflow."""

    redfish_servers: list[RedfishServer]
    updated_devices: list[HostDeviceData]


@workflow.defn
class RedfishProvisioningWorkflow(WorkflowMetadataMixin, StageMixin, ArchiveMixin):
    """Redfish BMC discovery and provisioning workflow for server management."""

    # Workflow metadata
    workflow_name = "Redfish Provisioning"
    workflow_description = "Discover and provision Redfish-capable BMCs for server management"
    workflow_input_class = RedfishProvisioningInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/ngc/redfish_provisioning"
    workflow_namespace = "ngc"

    def __init__(self) -> None:
        """Workflow Constructor."""
        super().__init__()
        self._write_to_dcim_stage_name = (
            "write_to_dcim"
            if workflow.patched(DCIM_STAGE_IDENTIFIERS_PATCH)
            else "write_to_nautobot"
        )
        self.define_stage(
            name="get_bmc_switches",
            description="Get BMC devices from the DCIM",
            requires_approval=False,
            depends_on=[],
        )
        self.define_stage(
            name="discover_hosts",
            description="Find online hosts",
            requires_approval=False,
            depends_on=["get_bmc_switches"],
        )
        self.define_stage(
            name="set_host_password",
            description="Provision redfish passwords",
            requires_approval=False,
            depends_on=["discover_hosts"],
        )
        self.define_stage(
            name="power_on_host",
            description="Power on the hosts",
            requires_approval=False,
            depends_on=["set_host_password"],
        )
        self.define_stage(
            name="wait_for_power_on",
            description="Wait 5 minutes for hosts to power on",
            requires_approval=False,
            depends_on=["power_on_host"],
        )
        self.define_stage(
            name="discover_addl_hosts",
            description="Find any newly online Redfish hosts",
            requires_approval=False,
            depends_on=["wait_for_power_on"],
        )
        self.define_stage(
            name="set_addl_host_password",
            description="Provision Redfish passwords on new hosts",
            requires_approval=False,
            depends_on=["discover_addl_hosts"],
        )
        self.define_stage(
            name="power_on_addl_hosts",
            description="Power on new hosts",
            requires_approval=False,
            depends_on=["set_addl_host_password"],
        )
        self.define_stage(
            name="wait_for_addl_power_on",
            description="Wait 5 minutes for additional hosts to power on",
            requires_approval=False,
            depends_on=["power_on_addl_hosts"],
        )
        self.define_stage(
            name="discover_host_details",
            description="Discover details of hosts",
            requires_approval=False,
            depends_on=["wait_for_addl_power_on"],
        )
        self.define_stage(
            name="update_dpu_mapping",
            description="Update Server to DPU mapping",
            requires_approval=False,
            depends_on=["discover_host_details"],
        )
        self.define_stage(
            name=self._write_to_dcim_stage_name,
            description="Update the DCIM with discovered host data",
            requires_approval=False,
            depends_on=["update_dpu_mapping"],
        )
        self.define_stage(
            name="approve_factory_reset",
            description="Approval to Factory reset all host BMCs",
            requires_approval=True,
            approval_threshold=1,
            depends_on=[self._write_to_dcim_stage_name],
        )
        self.define_stage(
            name="factory_reset_hosts",
            description="Factory reset all host BMCs",
            requires_approval=False,
            depends_on=["approve_factory_reset"],
        )

    class GetBmcSwitchStageInput(StageInput):
        """Get BMC device stage input."""

        site: str
        roles: list[str]

    class GetBmcSwitchStageOutput(StageOutput):
        """Get BMC device stage output."""

        devices: list[NetworkDeviceData]

    @stage_executor("get_bmc_switches")
    async def get_bmc_switches(
        self, stage_input: GetBmcSwitchStageInput
    ) -> GetBmcSwitchStageOutput:
        """Get BMC devices stage."""
        result = await workflow.execute_activity(
            get_network_devices,
            GetNetworkDevicesInput(
                site=stage_input.site,
                roles=stage_input.roles,
            ),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        return self.GetBmcSwitchStageOutput(
            devices=result.devices,
            display=self.markdown_table(result.devices),
        )

    class DiscoverHostsStageInput(StageInput):
        """Discover hosts stage input."""

        bmc_devices: list[NetworkDeviceData]
        ip_range_start: str
        ip_range_end: str
        ips_excluded: list[str]
        port: int
        timeout: int

    class RedfishStageInput(StageInput):
        """Redfish stage input."""

        hosts: list[RedfishHost]

    class RedfishStageOutput(StageOutput):
        """Redfish host stage output."""

        hosts: list[RedfishHost]

    async def discover_redfish(self, stage_input: DiscoverHostsStageInput) -> RedfishStageOutput:
        """Discover redfish hosts."""
        hosts = await workflow.execute_activity(
            discover_redfish_hosts,
            DiscoverHostsInput(
                ip_range_start=stage_input.ip_range_start,
                ip_range_end=stage_input.ip_range_end,
                ips_excluded=stage_input.ips_excluded,
                port=stage_input.port,
                timeout=stage_input.timeout,
            ),
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        arp_tables = await asyncio.gather(
            *[
                workflow.execute_activity(
                    get_device_arp_table,
                    device,
                    start_to_close_timeout=timedelta(minutes=1),
                    retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
                )
                for device in stage_input.bmc_devices
            ]
        )
        result = await workflow.execute_activity(
            populate_redfish_macs,
            PopulateRedfishMacsInput(
                hosts=hosts.hosts,
                arp_tables=arp_tables,
            ),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=ACTIVITY_NO_RETRY_POLICY,
        )
        return RedfishProvisioningWorkflow.RedfishStageOutput(
            hosts=result.hosts,
            display="Discovered Redfish hosts:\n" + self.markdown_table(result.hosts),
        )

    @stage_executor("discover_hosts")
    async def discover_hosts(self, stage_input: DiscoverHostsStageInput) -> RedfishStageOutput:
        """Discover hosts stage."""
        return await self.discover_redfish(stage_input=stage_input)

    @stage_executor("discover_addl_hosts")
    async def discover_addl_hosts(self, stage_input: DiscoverHostsStageInput) -> RedfishStageOutput:
        """Discover DPUs stage."""
        return await self.discover_redfish(stage_input=stage_input)

    async def set_password(self, stage_input: RedfishStageInput) -> RedfishStageOutput:
        """Set Redfish password."""
        activities = []
        for host in stage_input.hosts:
            if not host.vendor == RedfishVendor.DELL:
                activities.append(
                    workflow.execute_activity(
                        set_redfish_password,
                        RedfishHostInput(host=host),
                        start_to_close_timeout=timedelta(minutes=1),
                        retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
                    )
                )
        results = [result.host for result in await asyncio.gather(*activities) if result.host]
        return self.RedfishStageOutput(
            hosts=results,
            display="Redfish password set for the following devices:\n"
            + self.markdown_table(results),
        )

    @stage_executor("set_host_password")
    async def set_host_password(self, stage_input: RedfishStageInput) -> RedfishStageOutput:
        """Set host password stage."""
        return await self.set_password(stage_input=stage_input)

    @stage_executor("set_addl_host_password")
    async def set_addl_host_password(self, stage_input: RedfishStageInput) -> RedfishStageOutput:
        """Set aditional host password stage."""
        return await self.set_password(stage_input=stage_input)

    async def power_on(self, stage_input: RedfishStageInput) -> RedfishStageOutput:
        """Power on device."""
        activities = []
        for host in stage_input.hosts:
            activities.append(
                workflow.execute_activity(
                    power_on_host,
                    RedfishHostInput(host=host),
                    start_to_close_timeout=timedelta(minutes=15),
                    retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
                )
            )
        results = [result.host for result in await asyncio.gather(*activities) if result.host]
        return self.RedfishStageOutput(
            hosts=results,
            display="Powered on the following devices:\n" + self.markdown_table(results),
        )

    @stage_executor("power_on_host")
    async def power_on_host(self, stage_input: RedfishStageInput) -> RedfishStageOutput:
        """Power on host stage."""
        return await self.power_on(stage_input=stage_input)

    @stage_executor("power_on_addl_hosts")
    async def power_on_addl_hosts(self, stage_input: RedfishStageInput) -> RedfishStageOutput:
        """Power on additional host stage."""
        return await self.power_on(stage_input=stage_input)

    async def wait(self, stage_input: RedfishStageInput) -> RedfishStageOutput:
        """Wait for hosts to power on."""
        if not stage_input.hosts:
            return self.RedfishStageOutput(hosts=[], display="No hosts powered on, skipping.")
        await asyncio.sleep(300)
        return self.RedfishStageOutput(
            hosts=stage_input.hosts,
            display="Hosts powered on:\n" + self.markdown_table(stage_input.hosts),
        )

    @stage_executor("wait_for_power_on")
    async def wait_for_power_on(self, stage_input: RedfishStageInput) -> RedfishStageOutput:
        """Wait for hosts to power on."""
        return await self.wait(stage_input=stage_input)

    @stage_executor("wait_for_addl_power_on")
    async def wait_for_addl_power_on(self, stage_input: RedfishStageInput) -> RedfishStageOutput:
        """Wait for additional hosts to power on."""
        return await self.wait(stage_input=stage_input)

    class DiscoverServerDetailsStageInput(StageInput):
        """Discover server details stage input."""

        hosts: list[RedfishHost]
        nic_manufacturers: list[str]

    class DiscoverServerDetailsStageOutput(StageOutput):
        """Discover server details stage output."""

        servers: list[RedfishServer]
        dpus: list[RedfishDpu]

    @stage_executor("discover_host_details")
    async def discover_host_details(
        self, stage_input: DiscoverServerDetailsStageInput
    ) -> DiscoverServerDetailsStageOutput:
        """Discover server details stage."""
        server_results = await asyncio.gather(
            *[
                workflow.execute_activity(
                    get_server_details,
                    GetServerDetailsActivityInput(
                        host=host,
                        nic_manufacturers=stage_input.nic_manufacturers,
                    ),
                    start_to_close_timeout=timedelta(minutes=3),
                    retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
                )
                for host in stage_input.hosts
                if host.vendor != RedfishVendor.BLUEFIELD
            ]
        )
        dpu_results = await asyncio.gather(
            *[
                workflow.execute_activity(
                    get_dpu_details,
                    GetDpuDetailsActivityInput(host=host),
                    start_to_close_timeout=timedelta(minutes=3),
                    retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
                )
                for host in stage_input.hosts
                if host.vendor == RedfishVendor.BLUEFIELD
            ]
        )

        servers = [result.server for result in server_results]
        dpus = [result.dpu for result in dpu_results]
        return self.DiscoverServerDetailsStageOutput(
            servers=servers,
            dpus=dpus,
            display=self.markdown_table(servers) + self.markdown_table(dpus),
        )

    class UpdateDpuMappingStageInput(StageInput):
        """Update DPU mapping stage input."""

        servers: list[RedfishServer]
        dpus: list[RedfishDpu]

    class UpdateDpuMappingStageOutput(StageOutput):
        """Update DPU mapping stage output."""

        servers: list[RedfishServer]

    @stage_executor("update_dpu_mapping")
    async def update_dpu_mapping(
        self,
        stage_input: UpdateDpuMappingStageInput,
    ) -> UpdateDpuMappingStageOutput:
        """Update Server to DPU mapping stage."""
        base_macs = {dpu.base_mac: dpu for dpu in stage_input.dpus}

        results = copy.deepcopy(stage_input.servers)
        for server in results:
            for nic in server.nics:
                nic.dpu = base_macs.get(nic.mac) if nic.mac else None

        return self.UpdateDpuMappingStageOutput(
            servers=results, display=self.markdown_table(results)
        )

    class WriteToDCIMStageInput(StageInput):
        """Write to DCIM stage input."""

        servers: list[RedfishServer]

    class WriteToDCIMStageOutput(StageOutput):
        """Write to DCIM stage output."""

        updated_devices: list[HostDeviceData]

    @stage_executor("write_to_nautobot", name_attribute="_write_to_dcim_stage_name")
    async def write_to_dcim(
        self,
        stage_input: WriteToDCIMStageInput,
    ) -> WriteToDCIMStageOutput:
        """Write updated mappings to the DCIM."""
        results = await asyncio.gather(
            *[
                workflow.execute_activity(
                    update_dpu_data,
                    UpdateDpuDataActivityInput(server=server),
                    retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
                    start_to_close_timeout=timedelta(minutes=5),
                )
                for server in stage_input.servers
            ]
        )
        updated_devices = [data for result in results for data in result.device_data]
        return self.WriteToDCIMStageOutput(
            updated_devices=updated_devices,
            display=self.markdown_table(updated_devices),
        )

    @stage_executor("approve_factory_reset")
    async def approve_factory_reset(self, stage_input: RedfishStageInput) -> RedfishStageOutput:
        """Approve factory reset stage."""
        stage_name = "approve_factory_reset"
        output = self.RedfishStageOutput(
            hosts=stage_input.hosts,
            display="Approval needed to factory reset the following BMCs:\n"
            + self.markdown_table(stage_input.hosts),
        )
        self.set_stage_output(stage_name, output)
        self.set_stage_state(stage_name, StateEnum.PENDING_APPROVAL)
        await workflow.wait_condition(
            lambda: self.get_stage_state(stage_name) != StateEnum.PENDING_APPROVAL
        )

        approved = self.get_stage_state(stage_name) == StateEnum.APPROVED
        if approved:
            approval_state = "Approved"
            reviewers = [approver.user for approver in self.get_stage_by_name(stage_name).approvers]
        else:
            approval_state = "Rejected"
            reviewers = [rejecter.user for rejecter in self.get_stage_by_name(stage_name).rejecters]
        reviewmd = ",".join(reviewers)
        return self.RedfishStageOutput(
            hosts=stage_input.hosts,
            display=f"Factory Resets {approval_state} by {reviewmd} for devices:\n"
            + self.markdown_table(stage_input.hosts),
        )

    @stage_executor("factory_reset_hosts")
    async def factory_reset_hosts(self, stage_input: RedfishStageInput) -> RedfishStageOutput:
        """Factory reset Redfish manager."""
        activities = []
        for host in stage_input.hosts:
            if not host.vendor == RedfishVendor.DELL:
                activities.append(
                    workflow.execute_activity(
                        factory_reset_bmc,
                        RedfishHostInput(host=host),
                        start_to_close_timeout=timedelta(minutes=15),
                        retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
                    )
                )
        results = [result.host for result in await asyncio.gather(*activities) if result.host]
        return self.RedfishStageOutput(
            hosts=results,
            display="Factory reset complete for hosts:\n" + self.markdown_table(results),
        )

    @run_nv_config_manager_workflow
    async def run(  # type: ignore[override, ty:invalid-method-override]
        self,
        workflow_input: RedfishProvisioningInput,
    ) -> RedfishProvisioningResult:
        """Run the Redfish provisioning workflow."""
        self.set_input(workflow_input)

        bmc_devices = await self.get_bmc_switches(
            self.GetBmcSwitchStageInput(
                site=workflow_input.site,
                roles=workflow_input.bmc_switch_roles,
            )
        )

        discovery_results = await self.discover_hosts(
            self.DiscoverHostsStageInput(
                bmc_devices=bmc_devices.devices,
                ip_range_start=workflow_input.ip_range_start,
                ip_range_end=workflow_input.ip_range_end,
                ips_excluded=[],
                port=workflow_input.port,
                timeout=workflow_input.http_timeout_s,
            )
        )

        await self.set_host_password(self.RedfishStageInput(hosts=discovery_results.hosts))
        power_on_results = await self.power_on_host(
            self.RedfishStageInput(hosts=discovery_results.hosts)
        )

        await self.wait_for_power_on(self.RedfishStageInput(hosts=power_on_results.hosts))

        addl_discovery_results = await self.discover_addl_hosts(
            self.DiscoverHostsStageInput(
                ip_range_start=workflow_input.ip_range_start,
                ip_range_end=workflow_input.ip_range_end,
                ips_excluded=[h.address for h in discovery_results.hosts],
                port=workflow_input.port,
                bmc_devices=bmc_devices.devices,
                timeout=workflow_input.http_timeout_s,
            )
        )

        await self.set_addl_host_password(
            self.RedfishStageInput(hosts=addl_discovery_results.hosts)
        )

        power_on_addl_results = await self.power_on_addl_hosts(
            self.RedfishStageInput(hosts=addl_discovery_results.hosts)
        )

        await self.wait_for_addl_power_on(self.RedfishStageInput(hosts=power_on_addl_results.hosts))

        all_hosts = discovery_results.hosts + addl_discovery_results.hosts

        host_details = await self.discover_host_details(
            self.DiscoverServerDetailsStageInput(
                hosts=all_hosts, nic_manufacturers=NIC_MANUFACTURER_MELLANOX
            )
        )

        mapped_hosts = await self.update_dpu_mapping(
            self.UpdateDpuMappingStageInput(servers=host_details.servers, dpus=host_details.dpus)
        )

        updated_devices = await self.write_to_dcim(
            self.WriteToDCIMStageInput(servers=mapped_hosts.servers)
        )

        hosts_to_reset = [host for host in all_hosts if not host.vendor == RedfishVendor.DELL]

        await self.approve_factory_reset(self.RedfishStageInput(hosts=hosts_to_reset))
        if hosts_to_reset:
            await self.factory_reset_hosts(self.RedfishStageInput(hosts=hosts_to_reset))

        return RedfishProvisioningResult(
            redfish_servers=mapped_hosts.servers,
            updated_devices=updated_devices.updated_devices,
        )
