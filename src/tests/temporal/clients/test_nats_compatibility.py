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
"""Compatibility contracts for the configuration-backed Temporal NATS producer."""

from unittest.mock import patch

from nv_config_manager.common.client import NatsClient as CommonNatsClient
from nv_config_manager.common.client import NatsProducer as CommonNatsProducer
from nv_config_manager.temporal.client.nats import NatsProducer as TemporalNatsProducer
from nv_config_manager_workflows.clients import NatsClient as WorkflowNatsClient
from nv_config_manager_workflows.clients import NatsProducer as WorkflowNatsProducer

NATS_SETTINGS = {
    "server": "nats://nats.example:4222",
    "queue": "workflow-queue",
    "local": True,
    "auth_method": "JWT",
    "user": "nats-user",
    "password": "nats-password",
    "creds_path": "/secrets/nats.creds",
    "default_stream_name": "workflow-events",
    "default_stream_subjects": ["workflow.>", "audit.event"],
    "api_prefix": "$JS.CUSTOM.API",
}

DEFAULT_NATS_SETTINGS = {
    "server": "nats://nats.example:4222",
    "queue": "nv-config-manager",
    "local": False,
    "auth_method": "password",
    "user": None,
    "password": None,
    "creds_path": None,
    "default_stream_name": "nv-config-manager",
    "default_stream_subjects": ["nv-config-manager.>"],
    "api_prefix": "$JS.API",
}


def test_legacy_paths_delegate_to_the_workflows_producer() -> None:
    """Both retained import paths resolve to the reusable implementation."""
    assert issubclass(CommonNatsClient, WorkflowNatsClient)
    assert issubclass(CommonNatsProducer, WorkflowNatsProducer)
    assert issubclass(CommonNatsProducer, CommonNatsClient)
    assert issubclass(TemporalNatsProducer, CommonNatsProducer)


def test_zero_argument_producer_translates_all_service_settings() -> None:
    """The legacy constructor delegates all service settings to the reusable client."""
    with patch(
        "nv_config_manager.temporal.client.nats.nats_client_settings",
        return_value=NATS_SETTINGS,
    ) as settings:
        producer = TemporalNatsProducer()

    settings.assert_called_once_with()
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
    with patch(
        "nv_config_manager.temporal.client.nats.nats_client_settings",
        return_value=DEFAULT_NATS_SETTINGS,
    ):
        producer = TemporalNatsProducer()

    assert producer.queue == "nv-config-manager"
    assert producer.local is False
    assert producer.auth_method == "password"
    assert producer.default_stream_name == "nv-config-manager"
    assert producer.default_stream_subjects == ["nv-config-manager.>"]
    assert producer.api_prefix == "$JS.API"
