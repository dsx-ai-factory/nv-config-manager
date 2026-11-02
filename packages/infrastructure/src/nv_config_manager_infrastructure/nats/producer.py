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
"""JetStream message producer."""

from nv_config_manager_logging import LogCategory, get_logger

from nv_config_manager_infrastructure.nats.client import NatsClient

logger = get_logger(__name__, category=LogCategory.NATS)


class NatsProducer(NatsClient):
    """NATS JetStream producer for publishing messages."""

    async def publish(self, subject: str, message: str, stream: str | None = None) -> None:
        """Publish a message to a configured JetStream stream."""
        stream_name = stream or self.default_stream_name
        async with await self.connect() as conn:
            await conn.jetstream(prefix=self.api_prefix).publish(
                subject=subject, payload=message.encode("utf-8"), stream=stream_name
            )
            logger.debug("Published NATS message on stream %s subject %s", stream_name, subject)
