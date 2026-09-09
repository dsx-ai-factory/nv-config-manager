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
"""Certificate rotation workflow scheduler."""

import asyncio
import logging
import signal
from collections.abc import Sequence
from datetime import timedelta
from uuid import uuid4

from temporalio.client import Client, Schedule, ScheduleActionStartWorkflow, ScheduleSpec
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
from nv_config_manager.temporal.ngc.workflows.certificate_rotation import (
    CertificateRotationInput,
    CertificateRotationWorkflow,
)
from nv_config_manager.temporal.telemetry import get_runtime, setup_telemetry


class CertificateRotationScheduler:
    """Reconcile nightly certificate rotation schedules from DCIM intent."""

    SCHEDULE_PREFIX = "certificate-rotation-"
    SPEC = ScheduleSpec(
        cron_expressions=["0 2 * * *"],
        jitter=timedelta(hours=2),
        time_zone_name="UTC",
    )

    logger = get_logger(__name__, category=LogCategory.TEMPORAL_WORKFLOW)

    async def temporal_client(self) -> Client:
        """Connect to Temporal for schedule reconciliation."""
        return await Client.connect(
            temporal_address(),
            **client_connect_options(),
            data_converter=get_data_converter(),
            interceptors=[TracingInterceptor(always_create_workflow_spans=True)],
            runtime=get_runtime(),
        )

    async def devices_to_schedule(self) -> set[str]:
        """Retrieve devices with certificate assignments when PKI is configured."""
        config = load_config()
        if not config.has_section("pki"):
            return set()
        is_aggregate_env = config.getboolean(
            "aggregate", "is_aggregate_environment", fallback=False
        )
        client = create_dcim_client()
        async with client:
            operation = getattr(client, "get_certificate_enabled_device_ids", None)
            if operation is None:
                return set()
            return set(await operation(is_aggregate_env))

    async def scheduled_devices(self, temporal_client: Client) -> set[str]:
        """Retrieve device IDs with an existing certificate rotation schedule."""
        devices: set[str] = set()
        async for schedule in await temporal_client.list_schedules():
            if schedule.id.startswith(self.SCHEDULE_PREFIX):
                devices.add(schedule.id.removeprefix(self.SCHEDULE_PREFIX))
        return devices

    async def schedule_device(self, device_uuid: str, temporal_client: Client) -> None:
        """Create a daily, jittered certificate rotation schedule for one device."""
        self.logger.info("Scheduling certificate rotation for %s", device_uuid)
        workflow_roles = RBACConfig().get_workflow_roles(CertificateRotationWorkflow.__name__)
        if not workflow_roles:
            self.logger.error(
                "No RBAC configuration found for %s", CertificateRotationWorkflow.__name__
            )
            return
        typed_search_attributes = TypedSearchAttributes(
            (
                SearchAttributePair(
                    SearchAttributeKey.for_keyword(USER_SEARCH_ATTRIBUTE),
                    "nv-config-manager-temporal",
                ),
                SearchAttributePair[Sequence[str]](
                    SearchAttributeKey.for_keyword_list(READ_ROLES_SEARCH_ATTRIBUTE),
                    sorted(workflow_roles["read_roles"]),
                ),
                SearchAttributePair[Sequence[str]](
                    SearchAttributeKey.for_keyword_list(EXECUTE_ROLES_SEARCH_ATTRIBUTE),
                    sorted(workflow_roles["execute_roles"]),
                ),
                SearchAttributePair[bool](
                    SearchAttributeKey.for_bool(PENDING_APPROVAL_SEARCH_ATTRIBUTE), False
                ),
                SearchAttributePair[bool](
                    SearchAttributeKey.for_bool(FAILED_STAGE_SEARCH_ATTRIBUTE), False
                ),
            )
        )
        await temporal_client.create_schedule(
            f"{self.SCHEDULE_PREFIX}{device_uuid}",
            Schedule(
                action=ScheduleActionStartWorkflow(
                    CertificateRotationWorkflow.run,
                    CertificateRotationInput(device_id=device_uuid),
                    id=str(uuid4()),
                    task_queue="default-task-queue",
                    execution_timeout=timedelta(minutes=35),
                    typed_search_attributes=typed_search_attributes,
                ),
                spec=self.SPEC,
            ),
        )

    async def unschedule_device(self, device_uuid: str, temporal_client: Client) -> None:
        """Delete a certificate schedule after its DCIM assignments are removed."""
        self.logger.info("Removing certificate rotation schedule for %s", device_uuid)
        handle = temporal_client.get_schedule_handle(f"{self.SCHEDULE_PREFIX}{device_uuid}")
        await handle.delete()

    async def reconcile_schedules(self) -> None:
        """Reconcile certificate rotation schedules against current DCIM intent."""
        temporal_client = await self.temporal_client()
        desired_devices = await self.devices_to_schedule()
        scheduled_devices = await self.scheduled_devices(temporal_client)

        schedules_to_add = desired_devices - scheduled_devices
        schedules_to_remove = scheduled_devices - desired_devices
        self.logger.info("Scheduling rotation for %s new devices.", len(schedules_to_add))
        self.logger.info("Removing rotation schedules for %s devices.", len(schedules_to_remove))

        for device in schedules_to_add:
            await self.schedule_device(device, temporal_client)
        for device in schedules_to_remove:
            await self.unschedule_device(device, temporal_client)

        self.logger.info("Certificate rotation scheduling updates complete.")

    async def run(self) -> None:
        """Continuously reconcile certificate rotation schedules."""
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
                        "Error querying certificate-enabled devices from the configured "
                        "DCIM, leaving rotation schedules unchanged."
                    )
                await asyncio.sleep(timedelta(minutes=10).seconds)
            except asyncio.CancelledError:
                break


def main() -> None:
    """Run the certificate rotation scheduler service."""
    logging.basicConfig(level=logging.INFO)
    setup_telemetry("nv-config-manager-certificate-rotation-scheduler")
    asyncio.run(CertificateRotationScheduler().run())


if __name__ == "__main__":
    main()
