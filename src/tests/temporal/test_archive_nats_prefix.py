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
"""Tests that the archive consumer follows its stream's JetStream API prefix."""

from collections.abc import Callable
from unittest.mock import AsyncMock, patch

import pytest

from nv_config_manager.temporal.archive.main import main
from nv_config_manager.temporal.client.nats import NatsClient, NatsConsumer, NatsProducer
from nv_config_manager.temporal.ngc.activities.nats import PublishNatsInput, publish_nats
from nv_config_manager.temporal.runtime import configure_workflow_runtime
from nv_config_manager_workflows import runtime as runtime_module

BASE_NATS_CONFIG = """
[nats]
server = nats://nats.example.local:4222
queue = nv-config-manager
auth_method = none
config_manager_stream = nv-config-manager
archive_stream = nv-config-manager
archive_subject = nv-config-manager.workflow.result
"""

PREFIXED_NATS_CONFIG = BASE_NATS_CONFIG + "config_manager_api_prefix = $JS.CUSTOM.API\n"
NAMED_NATS_CONFIG = BASE_NATS_CONFIG + "archive_consumer_name = externally-managed-archive\n"


def _consumer() -> NatsConsumer:
    return NatsConsumer(
        stream="nv-config-manager",
        subject="nv-config-manager.workflow.result",
        queue_suffix="archive",
        handler=AsyncMock(),
    )


@pytest.mark.parametrize("client_type", [NatsClient, NatsProducer])
def test_temporal_nats_clients_use_service_settings(
    custom_ini: Callable[[str], None],
    client_type: type[NatsClient] | type[NatsProducer],
) -> None:
    custom_ini(PREFIXED_NATS_CONFIG + "config_manager_subjects = one, two\n")

    client = client_type()

    assert client.api_prefix == "$JS.CUSTOM.API"
    assert client.default_stream_subjects == ["one", "two"]


def test_consumer_defaults_to_standard_prefix(custom_ini: Callable[[str], None]) -> None:
    """An unset prefix leaves the consumer on the JetStream default."""
    custom_ini(BASE_NATS_CONFIG)
    assert _consumer().api_prefix == "$JS.API"


def test_consumer_follows_config_manager_prefix(custom_ini: Callable[[str], None]) -> None:
    """Archive events are a subject on the config-manager stream, so they share its prefix."""
    custom_ini(PREFIXED_NATS_CONFIG)
    assert _consumer().api_prefix == "$JS.CUSTOM.API"


def test_consumer_uses_fixed_default_name(custom_ini: Callable[[str], None]) -> None:
    """Archive identity does not inherit the site-specific queue prefix."""
    custom_ini(BASE_NATS_CONFIG.replace("queue = nv-config-manager", "queue = site-42"))
    assert _consumer().full_queue_name == "nv-config-manager-archive"
    assert _consumer().deliver_subject == "nv-config-manager.archive.delivery"


def test_consumer_name_is_configurable(custom_ini: Callable[[str], None]) -> None:
    """Externally provisioned archive durable names are configurable."""
    custom_ini(NAMED_NATS_CONFIG)
    assert _consumer().full_queue_name == "externally-managed-archive"


def test_archive_main_does_not_override_the_stream_prefix(
    custom_ini: Callable[[str], None],
) -> None:
    """The entrypoint inherits the stream's prefix instead of supplying its own."""
    custom_ini(PREFIXED_NATS_CONFIG)

    with (
        patch("nv_config_manager.temporal.archive.main.NatsConsumer") as mock_consumer,
        patch("nv_config_manager.temporal.archive.main.configure_logging"),
        patch("nv_config_manager.temporal.archive.main.setup_telemetry"),
        patch("sys.argv", ["archive"]),
    ):
        main()

    kwargs = mock_consumer.call_args.kwargs
    assert kwargs["stream"] == "nv-config-manager"
    assert kwargs["subject"] == "nv-config-manager.workflow.result"
    assert "api_prefix" not in kwargs


@pytest.mark.asyncio
async def test_workflow_result_publish_subject_is_unchanged(
    custom_ini: Callable[[str], None], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Account routing must not rename the workflow-result data subject."""
    custom_ini(PREFIXED_NATS_CONFIG)
    producer = AsyncMock()
    monkeypatch.setattr(runtime_module, "_nats_provider", runtime_module._UNSET)
    monkeypatch.setattr(runtime_module, "_slack_provider", runtime_module._UNSET)
    monkeypatch.setattr(runtime_module, "_ui_base_url_provider", runtime_module._UNSET)

    with patch(
        "nv_config_manager.temporal.runtime.NatsProducer.from_config", return_value=producer
    ):
        configure_workflow_runtime()
        await publish_nats(PublishNatsInput(message='{"workflow_id":"workflow-1"}'))

    producer.publish.assert_awaited_once_with(
        "nv-config-manager.workflow.result",
        '{"workflow_id":"workflow-1"}',
        stream="nv-config-manager",
    )
