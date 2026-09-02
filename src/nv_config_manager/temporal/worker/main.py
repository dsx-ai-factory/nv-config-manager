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
"""Temporal Worker — NVIDIA Config Manager."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from temporalio.client import Client
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.worker import Worker

from nv_config_manager.common.lock import configure_workflow_lock_backend
from nv_config_manager.common.log import configure_logging
from nv_config_manager.temporal.client.connection import client_connect_options, temporal_address
from nv_config_manager.temporal.common.activities import REGISTERED_COMMON_ACTIVITIES
from nv_config_manager.temporal.converter import get_data_converter
from nv_config_manager.temporal.hello_world.activities import (
    REGISTERED_ACTIVITIES as HELLO_WORLD_REGISTERED_ACTIVITIES,
)
from nv_config_manager.temporal.hello_world.workflows import (
    LOCAL_TEST_WORKFLOWS as HELLO_WORLD_LOCAL_TEST_WORKFLOWS,
)
from nv_config_manager.temporal.hello_world.workflows import (
    REGISTERED_WORKFLOWS as HELLO_WORLD_REGISTERED_WORKFLOWS,
)
from nv_config_manager.temporal.ngc.activities import (
    REGISTERED_ACTIVITIES as NGC_REGISTERED_ACTIVITIES,
)
from nv_config_manager.temporal.ngc.workflows import (
    REGISTERED_WORKFLOWS as NGC_REGISTERED_WORKFLOWS,
)
from nv_config_manager.temporal.telemetry import setup_telemetry

configure_logging(service="temporal-worker")


def _enabled_env_flag(name: str) -> bool:
    """Return true when an environment flag is explicitly enabled."""
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


async def main() -> None:
    """Run the temporal worker."""
    runtime = setup_telemetry("nv-config-manager-temporal-worker")

    configure_workflow_lock_backend()

    client = await Client.connect(
        temporal_address(),
        **client_connect_options(),
        data_converter=get_data_converter(),
        interceptors=[TracingInterceptor(always_create_workflow_spans=True)],
        runtime=runtime,
    )

    # Every core workflow is relevant to every supported DCIM provider.
    all_activities = [
        *NGC_REGISTERED_ACTIVITIES,
        *HELLO_WORLD_REGISTERED_ACTIVITIES,
        *REGISTERED_COMMON_ACTIVITIES,
    ]
    workflows: list[type[Any]] = [*NGC_REGISTERED_WORKFLOWS, *HELLO_WORLD_REGISTERED_WORKFLOWS]
    if _enabled_env_flag("NVCM_ENABLE_LOCAL_TEST_WORKFLOWS"):
        workflows.extend(HELLO_WORLD_LOCAL_TEST_WORKFLOWS)

    # The TracingInterceptor is registered on the client above, which already
    # covers worker activity/workflow calls. Registering it again here would
    # double-instrument and emit duplicate spans.
    worker = Worker(
        client,
        task_queue="default-task-queue",
        workflows=workflows,
        activities=all_activities,  # type: ignore[arg-type]
        activity_executor=ThreadPoolExecutor(100),
    )

    await worker.run()


def cli_main() -> None:
    """CLI entrypoint for temporal worker."""
    asyncio.run(main())


if __name__ == "__main__":
    cli_main()
