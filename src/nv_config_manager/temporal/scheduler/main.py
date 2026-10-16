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
"""Process entry point for the workflow scheduler host."""

import asyncio

from nv_config_manager.common.log import LogCategory, configure_logging, get_logger
from nv_config_manager.temporal.runtime import (
    build_builtin_scheduler_runtime,
    build_scheduler_runtime,
    configure_workflow_runtime,
)
from nv_config_manager.temporal.scheduler.host import (
    configured_scheduler_identities,
    run_scheduler_service,
    select_scheduler_registrations,
)
from nv_config_manager.temporal.telemetry import setup_telemetry
from nv_config_manager.temporal.workflow_registry import (
    build_workflow_registry,
    log_workflow_registry,
)
from nv_config_manager_workflows.registration import BUILTIN_PLUGIN_NAME
from nv_config_manager_workflows.schedulers.runtime import (
    configure_builtin_scheduler_runtime,
    configure_scheduler_runtime,
)

logger = get_logger(__name__, category=LogCategory.TEMPORAL_WORKFLOW)


def main() -> None:
    """Run all discovered schedulers with service-provided capabilities."""
    configure_logging(service="temporal-scheduler")
    setup_telemetry("nv-config-manager-temporal-scheduler")
    configure_workflow_runtime()
    configure_scheduler_runtime(build_scheduler_runtime())
    registry = build_workflow_registry()
    log_workflow_registry(registry)
    enabled_identities = configured_scheduler_identities()
    registrations = select_scheduler_registrations(registry, enabled_identities)

    logger.info(
        "Enabled workflow schedulers: %s",
        [registration.identity for registration in registrations],
    )
    if any(registration.plugin == BUILTIN_PLUGIN_NAME for registration in registrations):
        configure_builtin_scheduler_runtime(build_builtin_scheduler_runtime())

    asyncio.run(run_scheduler_service(registrations))


if __name__ == "__main__":
    main()
