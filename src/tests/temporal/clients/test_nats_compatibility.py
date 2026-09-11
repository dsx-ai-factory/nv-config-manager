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
"""Compatibility contracts for configuration-backed Temporal NATS clients."""

from configparser import ConfigParser
from unittest.mock import patch

from nv_config_manager.temporal.client.nats import NatsProducer


def _config() -> ConfigParser:
    config = ConfigParser()
    config.read_dict(
        {
            "nats": {
                "server": "nats://nats.example:4222",
                "queue": "workflow-queue",
                "local": "true",
                "auth_method": "JWT",
                "user": "nats-user",
                "password": "nats-password",
                "creds_path": "/secrets/nats.creds",
                "config_manager_stream": "workflow-events",
                "config_manager_subjects": "workflow.>, audit.event, ,",
                "config_manager_api_prefix": "$JS.CUSTOM.API",
            }
        }
    )
    return config


def test_zero_argument_producer_translates_all_service_settings() -> None:
    """The legacy constructor retains its complete INI-to-client translation."""
    config = _config()

    with patch(
        "nv_config_manager.temporal.client.nats.load_config",
        return_value=config,
    ) as load_config:
        producer = NatsProducer()

    load_config.assert_called_once_with()
    assert producer.server == "nats://nats.example:4222"
    assert producer.queue == "workflow-queue"
    assert producer.local is True
    assert producer.auth_method == "JWT"
    assert producer.user == "nats-user"
    assert producer.password == "nats-password"
    assert producer.creds_path == "/secrets/nats.creds"
    assert producer.default_stream_name == "workflow-events"
    assert producer.default_stream_subjects == ["workflow.>", "audit.event"]
    assert producer.api_prefix == "$JS.CUSTOM.API"


def test_zero_argument_producer_preserves_configuration_defaults() -> None:
    """Missing optional settings retain the current compatibility defaults."""
    config = ConfigParser()
    config.read_dict({"nats": {"server": "nats://nats.example:4222"}})

    with patch(
        "nv_config_manager.temporal.client.nats.load_config",
        return_value=config,
    ):
        producer = NatsProducer()

    assert producer.queue == "nv-config-manager"
    assert producer.local is False
    assert producer.auth_method == "password"
    assert producer.default_stream_name == "nv-config-manager"
    assert producer.default_stream_subjects == ["nv-config-manager.>"]
    assert producer.api_prefix == "$JS.API"
