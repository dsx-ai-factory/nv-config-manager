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
"""Activities for NATS."""

import nats
import nats.errors
import nats.js.errors
from nv_config_manager_infrastructure.nats import nats_server_for_logging
from pydantic import BaseModel
from temporalio import activity

from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager_workflows.runtime import NatsNotConfiguredError, get_nats_runtime

logger = get_logger(__name__, category=LogCategory.NATS)

# Default subject used when no archive subject is configured.
ARCHIVE_SUBJECT = "nv-config-manager.workflow.result"


class PublishNatsInput(BaseModel):
    """Input for publish activity."""

    subject: str | None = None
    message: str


@activity.defn
async def publish_nats(activity_input: PublishNatsInput) -> None:
    """Publish a NATS message to the workflow result bus."""
    runtime = get_nats_runtime()
    subject = activity_input.subject or runtime.subject
    if not subject:
        raise NatsNotConfiguredError(
            "NATS subject is not configured and the activity input did not provide one"
        )
    logger.info(
        "Publishing to NATS stream=%s subject=%s (message_len=%d)",
        runtime.stream,
        subject,
        len(activity_input.message),
    )
    try:
        await runtime.publisher.publish(subject, activity_input.message, stream=runtime.stream)
    except (nats.errors.Error, nats.js.errors.Error) as error:
        logger.error(
            "NATS publish failed: subject=%s server=%s error=%s",
            subject,
            nats_server_for_logging(runtime.publisher.server),
            error,
            exc_info=True,
        )
        raise
