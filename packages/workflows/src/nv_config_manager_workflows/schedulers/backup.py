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
"""Provider-neutral reconciliation for scheduled configuration backups."""

from collections.abc import Sequence
from datetime import timedelta
from uuid import uuid4

from nv_config_manager_dcim import DCIMError
from nv_config_manager_logging import LogCategory, get_logger
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

from nv_config_manager_workflows.scheduler_identity import BACKUP_SCHEDULE_PREFIX
from nv_config_manager_workflows.schedulers.runtime import (
    get_builtin_scheduler_runtime,
    get_scheduler_runtime,
)
from nv_config_manager_workflows.search_attributes import (
    EXECUTE_ROLES_SEARCH_ATTRIBUTE,
    FAILED_STAGE_SEARCH_ATTRIBUTE,
    PENDING_APPROVAL_SEARCH_ATTRIBUTE,
    READ_ROLES_SEARCH_ATTRIBUTE,
    USER_SEARCH_ATTRIBUTE,
)
from nv_config_manager_workflows.workflows.backup import (
    BackupInput,
    BackupWorkflow,
    TriggerEnum,
)

SCHEDULER_USER = "nv-config-manager-temporal"


class BackupScheduler:
    """Keep Temporal backup schedules synchronized with the desired device set."""

    scheduler_identity = "builtin.backup"
    SPEC = ScheduleSpec(
        intervals=[ScheduleIntervalSpec(every=timedelta(hours=12))],
        jitter=timedelta(hours=1),
    )
    RECONCILIATION_INTERVAL_SECONDS = timedelta(minutes=10).seconds

    logger = get_logger(__name__, category=LogCategory.TEMPORAL_WORKFLOW)

    async def temporal_client(self) -> Client:
        """Connect a new Temporal client through the service host."""
        return await get_scheduler_runtime().temporal_client()

    async def devices_to_schedule(self) -> set[str]:
        """Retrieve the set of desired scheduled devices."""
        return await get_builtin_scheduler_runtime().desired_backup_devices()

    async def scheduled_devices(self, temporal_client: Client) -> set[str]:
        """Retrieve the set of currently scheduled devices."""
        devices = set()
        async for schedule in await temporal_client.list_schedules():
            # Registry validation reserves the backup prefix for plugin names,
            # so no plugin ``<identity>:<key>`` schedule ID can start with it.
            if schedule.id.startswith(BACKUP_SCHEDULE_PREFIX):
                devices.add(schedule.id.removeprefix(BACKUP_SCHEDULE_PREFIX))
        return devices

    async def schedule_device(
        self,
        device_uuid: str,
        temporal_client: Client,
    ) -> None:
        """Create the unchanged scheduled Backup workflow action for one device."""
        self.logger.info("Scheduling backups for %s", device_uuid)
        workflow_roles = get_scheduler_runtime().workflow_roles(BackupWorkflow.__name__)
        if workflow_roles is None:
            self.logger.error("No RBAC configuration found for %s", BackupWorkflow.__name__)
            return

        typed_search_attributes = TypedSearchAttributes(
            (
                SearchAttributePair(
                    SearchAttributeKey.for_keyword(USER_SEARCH_ATTRIBUTE),
                    SCHEDULER_USER,
                ),
                SearchAttributePair[Sequence[str]](
                    SearchAttributeKey.for_keyword_list(READ_ROLES_SEARCH_ATTRIBUTE),
                    sorted(workflow_roles.read_roles),
                ),
                SearchAttributePair[Sequence[str]](
                    SearchAttributeKey.for_keyword_list(EXECUTE_ROLES_SEARCH_ATTRIBUTE),
                    sorted(workflow_roles.execute_roles),
                ),
                SearchAttributePair[bool](
                    SearchAttributeKey.for_bool(PENDING_APPROVAL_SEARCH_ATTRIBUTE),
                    False,
                ),
                SearchAttributePair[bool](
                    SearchAttributeKey.for_bool(FAILED_STAGE_SEARCH_ATTRIBUTE),
                    False,
                ),
            )
        )

        await temporal_client.create_schedule(
            f"{BACKUP_SCHEDULE_PREFIX}{device_uuid}",
            Schedule(
                action=ScheduleActionStartWorkflow(
                    BackupWorkflow.run,
                    BackupInput(
                        device_id=device_uuid,
                        trigger=TriggerEnum.SCHEDULED,
                        user=SCHEDULER_USER,
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

    async def unschedule_device(
        self,
        device_uuid: str,
        temporal_client: Client,
    ) -> None:
        """Delete the scheduler-owned backup schedule for one device."""
        self.logger.info("Removing backup schedule for %s", device_uuid)
        handle = temporal_client.get_schedule_handle(f"{BACKUP_SCHEDULE_PREFIX}{device_uuid}")
        await handle.delete()

    async def reconcile_schedules(self) -> None:
        """Reconcile scheduled device backups against the desired device set."""
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
        """Reconcile forever while allowing task cancellation to propagate."""
        # Report missing startup configuration before the first reconciliation.
        runtime = get_scheduler_runtime()
        get_builtin_scheduler_runtime()
        while True:
            try:
                await self.reconcile_schedules()
            except DCIMError:
                self.logger.exception(
                    "Error querying desired backup devices from the configured DCIM, "
                    "leaving schedules unchanged."
                )
            await runtime.sleep(self.RECONCILIATION_INTERVAL_SECONDS)


__all__ = ["BackupScheduler", "SCHEDULER_USER"]
