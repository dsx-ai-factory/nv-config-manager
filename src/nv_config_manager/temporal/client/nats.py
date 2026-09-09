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
from nv_config_manager.common.config_loader import load_config
from nv_config_manager.common.http_config import config_manager_api_prefix


def _stream_subjects(raw_subjects: str) -> list[str]:
    return [subject.strip() for subject in raw_subjects.split(",") if subject.strip()]


class NatsClient(BaseNatsClient):
    """NATS Client for Temporal workflows."""

    def __init__(self) -> None:
        """Initialize the NATS client from config."""
        config = load_config()
        nats_config = config["nats"]

        # Handle configparser string booleans
        local_str = nats_config.get("local", "false")
        local = local_str.lower() == "true" if isinstance(local_str, str) else bool(local_str)

        super().__init__(
            api_prefix=config_manager_api_prefix(nats_config),
            server=nats_config["server"],
            queue=nats_config.get("queue", "nv-config-manager"),
            local=local,
            auth_method=nats_config.get("auth_method", "password"),
            user=nats_config.get("user"),
            password=nats_config.get("password"),
            creds_path=nats_config.get("creds_path"),
            default_stream_name=nats_config.get("config_manager_stream", "nv-config-manager"),
            default_stream_subjects=_stream_subjects(
                nats_config.get("config_manager_subjects", "nv-config-manager.>")
            ),
        )


class NatsProducer(BaseNatsProducer):
    """NATS Producer for Temporal workflows."""

    def __init__(self) -> None:
        """Initialize the NATS producer from config."""
        config = load_config()
        nats_config = config["nats"]

        local_str = nats_config.get("local", "false")
        local = local_str.lower() == "true" if isinstance(local_str, str) else bool(local_str)

        super().__init__(
            api_prefix=config_manager_api_prefix(nats_config),
            server=nats_config["server"],
            queue=nats_config.get("queue", "nv-config-manager"),
            local=local,
            auth_method=nats_config.get("auth_method", "password"),
            user=nats_config.get("user"),
            password=nats_config.get("password"),
            creds_path=nats_config.get("creds_path"),
            default_stream_name=nats_config.get("config_manager_stream", "nv-config-manager"),
            default_stream_subjects=_stream_subjects(
                nats_config.get("config_manager_subjects", "nv-config-manager.>")
            ),
        )


class NatsConsumer(BaseNatsConsumer):
    """NATS Consumer for Temporal workflows."""

    def __init__(
        self,
        stream: str,
        subject: str,
        queue_suffix: str,
        handler: Callable[[Msg], Awaitable[None]],
    ) -> None:
        """Initialize the consumer from config."""
        config = load_config()
        nats_config = config["nats"]

        local_str = nats_config.get("local", "false")
        local = local_str.lower() == "true" if isinstance(local_str, str) else bool(local_str)

        super().__init__(
            stream=stream,
            subject=subject,
            queue_suffix=queue_suffix,
            handler=handler,
            durable_name=nats_config.get(
                "archive_consumer_name", f"nv-config-manager-{queue_suffix}"
            ),
            deliver_subject=nats_config.get(
                "archive_deliver_subject", "nv-config-manager.archive.delivery"
            ),
            api_prefix=config_manager_api_prefix(nats_config),
            server=nats_config["server"],
            queue=nats_config.get("queue", "nv-config-manager"),
            local=local,
            auth_method=nats_config.get("auth_method", "password"),
            user=nats_config.get("user"),
            password=nats_config.get("password"),
            creds_path=nats_config.get("creds_path"),
            default_stream_name=nats_config.get("config_manager_stream", "nv-config-manager"),
            default_stream_subjects=_stream_subjects(
                nats_config.get("config_manager_subjects", "nv-config-manager.>")
            ),
        )
