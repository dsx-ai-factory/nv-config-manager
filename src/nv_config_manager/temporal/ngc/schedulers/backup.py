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
"""Backup workflow scheduler."""

import asyncio
import logging
import signal
from collections.abc import Sequence
from datetime import timedelta
from uuid import uuid4

from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleIntervalSpec,
    ScheduleSpec,
)
from temporalio.common import (
    SearchAttributeKey,
    SearchAttributePair,
    TypedSearchAttributes,
)
from temporalio.contrib.opentelemetry import TracingInterceptor

from nv_config_manager.common.config import load_config
from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.dcim import DCIMError, create_dcim_client
from nv_config_manager.temporal.client.connection import client_connect_options, temporal_address
from nv_config_manager.temporal.common.rbac_config import RBACConfig
from nv_config_manager.temporal.common.search_attributes import (
    EXECUTE_ROLES_SEARCH_ATTRIBUTE,
    FAILED_STAGE_SEARCH_ATTRIBUTE,
    PENDING_APPROVAL_SEARCH_ATTRIBUTE,
    READ_ROLES_SEARCH_ATTRIBUTE,
    USER_SEARCH_ATTRIBUTE,
)
from nv_config_manager.temporal.converter import get_data_converter
from nv_config_manager.temporal.ngc.workflows.backup import BackupInput, BackupWorkflow, TriggerEnum
from nv_config_manager.temporal.telemetry import get_runtime, setup_telemetry


class BackupScheduler:
    """Backup Workflow Scheduler."""

    SCHEDULE_PREFIX = "backup-"
    # Run every 12 hours, with a jitter of 1 hour to avoid all devices being scheduled at the same time
    SPEC = ScheduleSpec(
        intervals=[ScheduleIntervalSpec(every=timedelta(hours=12))],
        jitter=timedelta(hours=1),
    )

    logger = get_logger(__name__, category=LogCategory.TEMPORAL_WORKFLOW)

    async def temporal_client(self) -> Client:
        return await Client.connect(
            temporal_address(),
            **client_connect_options(),
            data_converter=get_data_converter(),
            interceptors=[TracingInterceptor(always_create_workflow_spans=True)],
            runtime=get_runtime(),
        )

    async def devices_to_schedule(self) -> set[str]:
        """Retrieve the set of desired scheduled devices."""
        config = load_config()
        # Default to False if aggregate key is not present
        is_aggregate_env = config.getboolean(
            "aggregate", "is_aggregate_environment", fallback=False
        )
        client = create_dcim_client()
        async with client:
            return await client.get_backup_enabled_device_ids(is_aggregate_env)  # type: ignore[attr-defined]

    async def scheduled_devices(self, temporal_client: Client) -> set[str]:
        """Retrieve the set of currently scheduled devices."""
        devices = set()
        async for schedule in await temporal_client.list_schedules():
            if schedule.id.startswith(self.SCHEDULE_PREFIX):
                devices.add(schedule.id.replace(self.SCHEDULE_PREFIX, ""))
        return devices

    async def schedule_device(self, device_uuid: str, temporal_client: Client) -> None:
        """Schedule a device backup workflow."""
        self.logger.info("Scheduling backups for %s", device_uuid)
        rbac_config = RBACConfig()
        workflow_roles = rbac_config.get_workflow_roles(BackupWorkflow.__name__)
        if not workflow_roles:
            self.logger.error("No RBAC configuration found for %s", BackupWorkflow.__name__)
            return

        # Create properly typed search attributes
        user_key = SearchAttributeKey.for_keyword(USER_SEARCH_ATTRIBUTE)
        read_roles_key = SearchAttributeKey.for_keyword_list(READ_ROLES_SEARCH_ATTRIBUTE)
        execute_roles_key = SearchAttributeKey.for_keyword_list(EXECUTE_ROLES_SEARCH_ATTRIBUTE)
        pending_approval_key = SearchAttributeKey.for_bool(PENDING_APPROVAL_SEARCH_ATTRIBUTE)
        failed_stage_key = SearchAttributeKey.for_bool(FAILED_STAGE_SEARCH_ATTRIBUTE)

        typed_search_attributes = TypedSearchAttributes(
            (
                SearchAttributePair(user_key, "nv-config-manager-temporal"),
                SearchAttributePair[Sequence[str]](
                    read_roles_key, sorted(workflow_roles["read_roles"])
                ),
                SearchAttributePair[Sequence[str]](
                    execute_roles_key, sorted(workflow_roles["execute_roles"])
                ),
                SearchAttributePair[bool](pending_approval_key, False),
                SearchAttributePair[bool](failed_stage_key, False),
            )
        )

        await temporal_client.create_schedule(
            f"{self.SCHEDULE_PREFIX}{device_uuid}",
            Schedule(
                action=ScheduleActionStartWorkflow(
                    BackupWorkflow.run,
                    BackupInput(
                        device_id=device_uuid,
                        trigger=TriggerEnum.SCHEDULED,
                        user="nv-config-manager-temporal",
                        user_domain=None,
                        workflow_id=None,
                        intended_config_commit_id=None,
                    ),
                    id=str(uuid4()),
                    task_queue="default-task-queue",
                    execution_timeout=timedelta(minutes=30),
                    typed_search_attributes=typed_search_attributes,
                ),
                spec=self.SPEC,
            ),
        )

    async def unschedule_device(self, device_uuid: str, temporal_client: Client) -> None:
        """Unschedule a device backup."""
        self.logger.info("Removing backup schedule for %s", device_uuid)
        handle = temporal_client.get_schedule_handle(f"{self.SCHEDULE_PREFIX}{device_uuid}")
        await handle.delete()

    async def reconcile_schedules(self) -> None:
        """Reconcile the scheduled device backups against desired backups."""
        temporal_client = await self.temporal_client()
        desired_devices = await self.devices_to_schedule()
        scheduled_devices = await self.scheduled_devices(temporal_client)

        schedules_to_add = desired_devices - scheduled_devices
        schedules_to_remove = scheduled_devices - desired_devices
        self.logger.info("Scheduling %s new device backups.", len(schedules_to_add))
        self.logger.info("Removing schedule for %s device backups.", len(schedules_to_remove))

        for device in schedules_to_add:
            await self.schedule_device(device, temporal_client)

        for device in schedules_to_remove:
            await self.unschedule_device(device, temporal_client)

        self.logger.info("Backup scheduling updates complete.")

    async def run(self) -> None:
        """Run the backup scheduler."""
        loop = asyncio.get_running_loop()

        def stop_handler(signum: int, frame: object) -> None:
            self.logger.info("Received signal %s, stopping scheduler.", signum)
            loop.stop()

        for sig in [signal.SIGTERM, signal.SIGINT]:
            signal.signal(sig, stop_handler)

        while True:
            try:
                try:
                    await self.reconcile_schedules()
                except DCIMError:
                    self.logger.exception(
                        "Error querying desired backup devices from the configured DCIM, leaving schedules unchanged."
                    )
                await asyncio.sleep(timedelta(minutes=10).seconds)
            except asyncio.CancelledError:
                break


def main() -> None:
    """Entry point for nv-config-manager-temporal-scheduler command."""
    logging.basicConfig(level=logging.INFO)
    setup_telemetry("nv-config-manager-temporal-scheduler")
    asyncio.run(BackupScheduler().run())


if __name__ == "__main__":
    main()
