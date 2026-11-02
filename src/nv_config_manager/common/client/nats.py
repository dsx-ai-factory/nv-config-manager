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
"""Service INI factory and compatibility exports for nats."""

from __future__ import annotations

from configparser import ConfigParser, SectionProxy

from nv_config_manager_infrastructure.nats import DEFAULT_NATS_API_PREFIX
from nv_config_manager_infrastructure.nats import NatsClient as _NatsClient
from nv_config_manager_infrastructure.nats import NatsConsumer as _NatsConsumer
from nv_config_manager_infrastructure.nats import NatsProducer as _NatsProducer


def config_manager_api_prefix(nats_config: SectionProxy) -> str:
    """Return the JetStream API prefix for the stream owned by the config-manager account.

    A JetStream API prefix identifies the NATS account hosting a stream, so it is a
    property of the stream rather than of any individual subject on it.
    """
    return nats_config.get("config_manager_api_prefix", DEFAULT_NATS_API_PREFIX)


class NatsClient(_NatsClient):
    """Application client retaining the legacy INI factory."""

    @classmethod
    def from_config(cls, config: ConfigParser) -> NatsClient:
        """Create NatsClient from configuration.

        Args:
            config: ConfigParser with 'nats' section

        Returns:
            Configured NatsClient instance
        """
        nats_config = config["nats"]

        stream_subjects = [
            subject.strip()
            for subject in nats_config.get("config_manager_subjects", "nv-config-manager.>").split(
                ","
            )
            if subject.strip()
        ]
        return cls(
            server=nats_config["server"],
            queue=nats_config.get("queue", "nv-config-manager"),
            local=nats_config.getboolean("local", fallback=False),
            auth_method=nats_config.get("auth_method", "password"),
            user=nats_config.get("user"),
            password=nats_config.get("password"),
            creds_path=nats_config.get("creds_path"),
            default_stream_name=nats_config.get("config_manager_stream", "nv-config-manager"),
            default_stream_subjects=stream_subjects,
            api_prefix=config_manager_api_prefix(nats_config),
        )


class NatsConsumer(_NatsConsumer, NatsClient):
    """INI-configurable NATS consumer."""


class NatsProducer(_NatsProducer, NatsClient):
    """INI-configurable NATS producer."""
