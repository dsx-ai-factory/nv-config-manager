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
"""Diagnostics activities and public exports."""

import asyncio
import time
from contextlib import closing

from temporalio import activity

from nv_config_manager_workflows.activities.diagnostics.helpers import (
    COMMAND_DESCRIPTIONS,
    PLATFORM_COMMANDS,
    get_available_commands,
    validate_commands,
)
from nv_config_manager_workflows.activities.diagnostics.models import (
    RunDiagnosticsInput,
    RunDiagnosticsOutput,
    TechSupportInput,
    TechSupportOutput,
)
from nv_config_manager_workflows.runtime import (
    get_api_base_url,
    get_device_connection,
    get_redis_client,
)
from nv_config_manager_workflows.tech_support import (
    TECH_SUPPORT_BUNDLE_TTL,
    tech_support_key,
)


@activity.defn
def run_diagnostic_commands(activity_input: RunDiagnosticsInput) -> RunDiagnosticsOutput:
    valid_commands = validate_commands(
        activity_input.device_data.platform,
        activity_input.commands,
    )
    outputs: dict[str, str] = {}
    with closing(get_device_connection(activity_input.device_data)) as connection:
        for name in valid_commands:
            try:
                outputs[name] = connection.run_diagnostic_command(name)
            except Exception as e:
                # per-command failure captured, never aborts the device
                outputs[name] = f"ERROR: {e}"
    return RunDiagnosticsOutput(device_name=activity_input.device_data.name, outputs=outputs)


@activity.defn
def collect_tech_support_bundle(activity_input: TechSupportInput) -> TechSupportOutput:
    """Collect a cl-support bundle from a device and store its bytes in Redis.

    Sync activity — runs in the ThreadPoolExecutor so activity.heartbeat() is
    reliably delivered from the thread. The heartbeat_fn callback is called:
      - during quiet stretches of cl-support output (every ~20 s of silence)
      - during the SFTP download on each received chunk (rate-limited to 10 s)
    This keeps the 60-second heartbeat window well-satisfied throughout.

    Bytes are stored in Redis (not returned through Temporal) to avoid a ~25 MB
    JSON payload that would exceed the heartbeat window during result transmission.
    The activity returns only a Redis key and a download URL.
    """
    device_name = activity_input.device_data.name
    info = activity.info()
    workflow_id = info.workflow_id
    if workflow_id is None:
        raise RuntimeError("Tech-support collection requires a workflow ID")
    start = time.monotonic()

    def _heartbeat() -> None:
        elapsed = int(time.monotonic() - start)
        activity.heartbeat(f"Generating cl-support bundle on {device_name} ({elapsed}s elapsed)...")

    with closing(get_device_connection(activity_input.device_data)) as connection:
        content, cl_support_log = connection.get_tech_support_bundle(_heartbeat)

    # Store raw bytes in Redis; never transmit them through Temporal.
    redis_key = tech_support_key(workflow_id, device_name)
    api_base = get_api_base_url().rstrip("/")
    download_url = (
        f"{api_base}/v1/workflow/{workflow_id}/tech-support/{device_name}" if api_base else ""
    )

    async def _save() -> None:
        cache = get_redis_client()
        await cache.set(redis_key, content, ttl=TECH_SUPPORT_BUNDLE_TTL, serialize=False)

    asyncio.run(_save())

    activity.heartbeat(
        f"Bundle stored in Redis on {device_name} ({len(content)} bytes, "
        f"{int(time.monotonic() - start)}s total). Key: {redis_key}\n\n"
        f"cl-support output:\n{cl_support_log}"
    )
    return TechSupportOutput(
        device_name=device_name,
        redis_key=redis_key,
        download_url=download_url,
        cl_support_log=cl_support_log,
    )


DIAGNOSTICS_ACTIVITIES = (
    run_diagnostic_commands,
    collect_tech_support_bundle,
)

__all__ = [
    "COMMAND_DESCRIPTIONS",
    "DIAGNOSTICS_ACTIVITIES",
    "PLATFORM_COMMANDS",
    "RunDiagnosticsInput",
    "RunDiagnosticsOutput",
    "TechSupportInput",
    "TechSupportOutput",
    "collect_tech_support_bundle",
    "get_available_commands",
    "run_diagnostic_commands",
    "validate_commands",
]
