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
"""NATS Client for Temporal workflows.

Provides NATS producer and consumer for Temporal event publishing.
Uses the common NATS client as a base.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from nats.aio.msg import Msg

from nv_config_manager.common.client import (
    NatsClient as BaseNatsClient,
)
from nv_config_manager.common.client import (
    NatsConsumer as BaseNatsConsumer,
)
from nv_config_manager.common.client import (
    NatsProducer as BaseNatsProducer,
)
from nv_config_manager.temporal.factories.nats import (
    nats_client_settings,
    nats_consumer_settings,
)


class NatsClient(BaseNatsClient):
    """NATS Client for Temporal workflows."""

    def __init__(self) -> None:
        """Initialize the reusable client from service configuration."""
        super().__init__(**nats_client_settings())


class NatsProducer(BaseNatsProducer):
    """Compatibility adapter for the relocated NATS producer."""

    def __init__(self) -> None:
        """Initialize the reusable producer from service configuration."""
        super().__init__(**nats_client_settings())


class NatsConsumer(BaseNatsConsumer):
    """NATS Consumer for Temporal workflows."""

    def __init__(
        self,
        stream: str,
        subject: str,
        queue_suffix: str,
        handler: Callable[[Msg], Awaitable[None]],
    ) -> None:
        """Initialize the service-owned consumer from explicit adapter settings."""
        super().__init__(
            stream=stream,
            subject=subject,
            queue_suffix=queue_suffix,
            handler=handler,
            **nats_consumer_settings(queue_suffix),
        )
