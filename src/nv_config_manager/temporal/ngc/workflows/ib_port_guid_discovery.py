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
"""Workflow: discover UFM port GUIDs and sync them onto DCIM interfaces.

Per-UFM-host, dry-run by default. Pass dry_run=False to actually patch
`dcim.interface.cf_ib_guid`. Uses the DCIM provider's cable topology to resolve
compute-side interfaces rather than relying on UFM node descriptions
(OS hostnames on compute trays are not under our control).
"""

from datetime import timedelta

from pydantic import BaseModel, Field
from temporalio import workflow
from temporalio.common import RetryPolicy

from nv_config_manager.temporal.common.decorators.workflow import run_nv_config_manager_workflow
from nv_config_manager.temporal.common.mixins.metadata import WorkflowMetadataMixin
from nv_config_manager.temporal.common.mixins.stage import (
    StageInput,
    StageMixin,
    StageOutput,
    stage_executor,
)
from nv_config_manager.temporal.common.workflow_references import (
    DeviceReference,
    DeviceReferences,
)

with workflow.unsafe.imports_passed_through():
    from nv_config_manager.temporal.ngc.activities.dcim import (
        GetNetworkDeviceInput,
        get_network_device,
    )
    from nv_config_manager.temporal.ngc.activities.ib_guid_discovery import (
        DiscoverIBPortGuidsInput,
        DiscoverIBPortGuidsOutput,
        IBGuidMapping,
        SyncIBGuidInput,
        SyncIBGuidOutput,
        discover_ib_port_guids,
        sync_ib_guid_on_interface,
    )

DEFAULT_ACTIVITY_RETRY_POLICY = RetryPolicy(
    maximum_attempts=3,
    non_retryable_error_types=[],
)


class IBPortGuidDiscoveryInput(BaseModel):
    """Input for the IB Port GUID Discovery workflow."""

    ufm_device_id: DeviceReference = Field(
        description="Identifier of the UFM device used to discover port GUIDs."
    )
    switch_device_ids: DeviceReferences = Field(
        description="Identifiers of the InfiniBand switches whose interfaces will be synchronized."
    )
    dry_run: bool = Field(
        default=True, description="Whether to report changes without updating the DCIM."
    )


class IBPortGuidDiscoveryResult(BaseModel):
    """Result of the IB Port GUID Discovery workflow."""

    summary: str
    dry_run: bool
    mappings: list[IBGuidMapping]
    sync_results: list[SyncIBGuidOutput]
    applied_count: int
    would_apply_count: int
    skipped_count: int


@workflow.defn
class IBPortGuidDiscoveryWorkflow(WorkflowMetadataMixin, StageMixin):
    """Sync UFM-discovered IB port GUIDs onto matching DCIM interfaces."""

    workflow_name = "InfiniBand Port GUID Discovery"
    workflow_description = (
        "Discover InfiniBand port GUIDs from UFM and sync them onto the matching DCIM interfaces."
    )
    workflow_input_class = IBPortGuidDiscoveryInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/ngc/ib_port_guid_discovery"
    workflow_namespace = "ngc"

    def __init__(self) -> None:
        """Initialize workflow stages."""
        super().__init__()
        self.define_stage(
            name="resolve_ufm",
            description="Resolve UFM hostname and site from the DCIM.",
            requires_approval=False,
            depends_on=[],
        )
        self.define_stage(
            name="discover",
            description="Fetch UFM ports and DCIM topology and compute mappings.",
            requires_approval=False,
            depends_on=["resolve_ufm"],
        )
        self.define_stage(
            name="sync",
            description=("Sync ib_guid on each resolved interface."),
            requires_approval=False,
            depends_on=["discover"],
        )

    class ResolveUFMStageInput(StageInput):
        """Input for UFM device resolution."""

        ufm_device_id: str

    class ResolveUFMStageOutput(StageOutput):
        """UFM hostname and site from the DCIM."""

        ufm_hostname: str
        site: str

    class DiscoverStageInput(StageInput):
        """Input for discovery stage."""

        ufm_hostname: str
        site: str
        switch_device_ids: list[str]

    class DiscoverStageOutput(StageOutput):
        """Result of discovery stage."""

        mappings: list[IBGuidMapping]

    class SyncStageInput(StageInput):
        """Input for the sync stage."""

        mappings: list[IBGuidMapping]
        dry_run: bool

    class SyncStageOutput(StageOutput):
        """Result of the sync stage."""

        sync_results: list[SyncIBGuidOutput]
        applied_count: int
        would_apply_count: int
        skipped_count: int

    @stage_executor("resolve_ufm")
    async def resolve_ufm(self, stage_input: ResolveUFMStageInput) -> ResolveUFMStageOutput:
        """Resolve UFM device primary IP and site from the DCIM."""
        device_result = await workflow.execute_activity(
            get_network_device,
            GetNetworkDeviceInput(device_id=stage_input.ufm_device_id),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        if not device_result.device.host:
            raise ValueError("UFM device has no primary IP address set in the DCIM.")

        return IBPortGuidDiscoveryWorkflow.ResolveUFMStageOutput(
            ufm_hostname=device_result.device.host,
            site=device_result.device.site,
            display=(
                f"UFM resolved: host={device_result.device.host}, site={device_result.device.site}"
            ),
        )

    @stage_executor("discover")
    async def discover(self, stage_input: DiscoverStageInput) -> DiscoverStageOutput:
        """Run the discovery activity."""
        result: DiscoverIBPortGuidsOutput = await workflow.execute_activity(
            discover_ib_port_guids,
            DiscoverIBPortGuidsInput(
                ufm_host=stage_input.ufm_hostname,
                site=stage_input.site,
                switch_device_ids=stage_input.switch_device_ids,
            ),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        return IBPortGuidDiscoveryWorkflow.DiscoverStageOutput(
            mappings=result.mappings,
            display=result.display,
        )

    @stage_executor("sync")
    async def sync(self, stage_input: SyncStageInput) -> SyncStageOutput:
        """Invoke the sync activity per mapping."""
        sync_results: list[SyncIBGuidOutput] = []
        applied = 0
        would_apply = 0
        skipped = 0

        for mapping in stage_input.mappings:
            if mapping.action in ("noop", "skip") or not mapping.interface_id:
                skipped += 1
                continue

            result: SyncIBGuidOutput = await workflow.execute_activity(
                sync_ib_guid_on_interface,
                SyncIBGuidInput(
                    interface_id=mapping.interface_id,
                    guid=mapping.discovered_guid,
                    dry_run=stage_input.dry_run,
                ),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
            )
            sync_results.append(result)
            if result.dry_run:
                would_apply += 1
            elif result.changed:
                applied += 1
            else:
                skipped += 1

        total_mappings = len(stage_input.mappings)
        return IBPortGuidDiscoveryWorkflow.SyncStageOutput(
            sync_results=sync_results,
            applied_count=applied,
            would_apply_count=would_apply,
            skipped_count=skipped,
            display=(
                f"Sync complete (dry_run={stage_input.dry_run}): "
                f"applied={applied}, would_apply={would_apply}, "
                f"skipped={skipped}, total_mappings={total_mappings}"
            ),
        )

    @run_nv_config_manager_workflow
    async def run(self, workflow_input: IBPortGuidDiscoveryInput) -> IBPortGuidDiscoveryResult:  # type: ignore[override, ty:invalid-method-override]
        """Execute the IB Port GUID discovery workflow."""
        self.set_input(workflow_input)

        resolved = await self.resolve_ufm(
            IBPortGuidDiscoveryWorkflow.ResolveUFMStageInput(
                ufm_device_id=workflow_input.ufm_device_id,
            )
        )

        discovery = await self.discover(
            IBPortGuidDiscoveryWorkflow.DiscoverStageInput(
                ufm_hostname=resolved.ufm_hostname,
                site=resolved.site,
                switch_device_ids=workflow_input.switch_device_ids,
            )
        )

        synced = await self.sync(
            IBPortGuidDiscoveryWorkflow.SyncStageInput(
                mappings=discovery.mappings,
                dry_run=workflow_input.dry_run,
            )
        )

        summary = f"{discovery.display}\n{synced.display}" if synced.display else discovery.display
        return IBPortGuidDiscoveryResult(
            summary=summary,
            dry_run=workflow_input.dry_run,
            mappings=discovery.mappings,
            sync_results=synced.sync_results,
            applied_count=synced.applied_count,
            would_apply_count=synced.would_apply_count,
            skipped_count=synced.skipped_count,
        )
