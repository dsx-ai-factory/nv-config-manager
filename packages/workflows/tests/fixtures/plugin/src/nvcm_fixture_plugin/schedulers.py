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
"""The fixture plugin's scheduler."""

from datetime import timedelta

from temporalio.client import (
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleIntervalSpec,
    ScheduleSpec,
)

from nv_config_manager_workflows.schedulers.runtime import get_scheduler_runtime
from nv_config_manager_workflows.schedulers.schedule_ids import owns_schedule_id, schedule_id
from nvcm_fixture_plugin.workflows import FixtureApiOnlyWorkflow, FixtureInput

# The task queue the NVIDIA Config Manager worker polls.
TASK_QUEUE = "default-task-queue"


class FixtureHeartbeatScheduler:
    """Keep a daily heartbeat schedule in this scheduler's schedule-ID namespace."""

    scheduler_identity = "nvcm-fixture.heartbeat"

    async def run(self) -> None:
        """Create the heartbeat schedule whenever it is missing, until cancelled."""
        runtime = get_scheduler_runtime()
        heartbeat_id = schedule_id(self.scheduler_identity, "heartbeat")
        while True:
            client = await runtime.temporal_client()
            owned_ids = {
                schedule.id
                async for schedule in await client.list_schedules()
                if owns_schedule_id(self.scheduler_identity, schedule.id)
            }
            if heartbeat_id not in owned_ids:
                await client.create_schedule(
                    heartbeat_id,
                    Schedule(
                        action=ScheduleActionStartWorkflow(
                            FixtureApiOnlyWorkflow.run,
                            FixtureInput(message="heartbeat"),
                            id=heartbeat_id,
                            task_queue=TASK_QUEUE,
                        ),
                        spec=ScheduleSpec(
                            intervals=[ScheduleIntervalSpec(every=timedelta(days=1))]
                        ),
                    ),
                )
            await runtime.sleep(timedelta(minutes=10).total_seconds())


FIXTURE_SCHEDULERS = (FixtureHeartbeatScheduler,)

__all__ = ["FIXTURE_SCHEDULERS", "TASK_QUEUE", "FixtureHeartbeatScheduler"]
