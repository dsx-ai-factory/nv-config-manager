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

from nv_config_manager.common.log import configure_logging
from nv_config_manager.temporal.client.connection import client_connect_options, temporal_address
from nv_config_manager.temporal.converter import get_data_converter
from nv_config_manager.temporal.runtime import configure_workflow_runtime
from nv_config_manager.temporal.telemetry import setup_telemetry
from nv_config_manager.temporal.workflow_registry import (
    build_workflow_registry,
    log_workflow_registry,
)
from nv_config_manager_workflows.registration.registry import WorkflowRegistry
from nv_config_manager_workflows.workflows import LOCAL_TEST_WORKFLOWS

configure_logging(service="temporal-worker")


def _enabled_env_flag(name: str) -> bool:
    """Return true when an environment flag is explicitly enabled."""
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _registered_workflows(registry: WorkflowRegistry) -> list[type[Any]]:
    """Return a copy of the registry's workflows, plus the local-test workflows when opted in."""
    workflows: list[type[Any]] = list(registry.all_workflows)
    if _enabled_env_flag("NVCM_ENABLE_LOCAL_TEST_WORKFLOWS"):
        workflows.extend(LOCAL_TEST_WORKFLOWS)
    return workflows


async def main() -> None:
    """Run the temporal worker."""
    configure_workflow_runtime()
    runtime = setup_telemetry("nv-config-manager-temporal-worker")

    client = await Client.connect(
        temporal_address(),
        **client_connect_options(),
        data_converter=get_data_converter(),
        interceptors=[TracingInterceptor(always_create_workflow_spans=True)],
        runtime=runtime,
    )

    registry = build_workflow_registry()
    workflows = _registered_workflows(registry)
    log_workflow_registry(registry)

    # The TracingInterceptor is registered on the client above, which already
    # covers worker activity/workflow calls. Registering it again here would
    # double-instrument and emit duplicate spans.
    worker = Worker(
        client,
        task_queue="default-task-queue",
        workflows=workflows,
        activities=registry.all_activities,  # type: ignore[arg-type]
        activity_executor=ThreadPoolExecutor(100),
    )

    await worker.run()


def cli_main() -> None:
    """CLI entrypoint for temporal worker."""
    asyncio.run(main())


if __name__ == "__main__":
    cli_main()
