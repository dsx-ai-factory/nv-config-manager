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
"""NATS JetStream producer."""

import logging

from nv_config_manager_workflows.clients.nats.base import NatsClient

logger = logging.getLogger(__name__)


class NatsProducer(NatsClient):
    """Publish messages using an explicitly configured NATS client."""

    async def publish(self, subject: str, message: str, stream: str | None = None) -> None:
        """Publish a UTF-8 message to the selected JetStream stream."""
        stream_name = stream or self.default_stream_name
        async with await self.connect() as connection:
            await connection.jetstream(prefix=self.api_prefix).publish(
                subject=subject,
                payload=message.encode("utf-8"),
                stream=stream_name,
            )
            logger.debug("Published NATS message on stream %s subject %s", stream_name, subject)
