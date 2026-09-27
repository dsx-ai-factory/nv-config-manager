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
"""Freeze the final diagnostics and ticketing extraction contracts."""

import inspect
from collections.abc import Callable
from typing import Any

from nv_config_manager_dcim.workflow_models import NetworkDeviceData, Platform

from nv_config_manager.temporal.ngc.activities import diagnostics as legacy_diagnostics
from nv_config_manager.temporal.ngc.activities import ticketing as legacy_ticketing
from nv_config_manager_workflows.activities import diagnostics, ticketing
from nv_config_manager_workflows.registration.contract import activity_name

DEVICE = NetworkDeviceData(
    id="device-id",
    name="switch-1",
    role="leaf",
    platform=Platform.CUMULUS_LINUX,
    site="site-1",
    device_type="switch",
    primary_ip4="192.0.2.10",
    primary_ip6=None,
)


def test_diagnostics_activity_names_signatures_and_callable_forms_are_frozen() -> None:
    """Moving the callables does not alter Temporal names or sync/async execution."""
    expected: dict[Callable[..., Any], tuple[str, bool]] = {
        diagnostics.run_diagnostic_commands: ("run_diagnostic_commands", False),
        diagnostics.collect_tech_support_bundle: ("collect_tech_support_bundle", False),
        ticketing.validate_ticket: ("validate_ticket", True),
        ticketing.upload_attachment: ("upload_attachment", True),
        ticketing.upload_tech_support_from_redis: ("upload_tech_support_from_redis", True),
        ticketing.add_ticket_comment: ("add_ticket_comment", True),
    }
    for function, (name, is_async) in expected.items():
        assert activity_name(function) == name
        assert inspect.iscoroutinefunction(function) is is_async
        parameters = tuple(inspect.signature(function).parameters.values())
        assert len(parameters) == 1
        assert parameters[0].name == "activity_input"


def test_diagnostics_model_serialization_contract_is_frozen() -> None:
    """Representative payloads preserve field names, defaults, and byte coercion."""
    assert diagnostics.RunDiagnosticsInput(
        device_data=DEVICE, commands=["show_version"]
    ).model_dump(mode="json") == {
        "device_data": DEVICE.model_dump(mode="json"),
        "commands": ["show_version"],
    }
    assert diagnostics.TechSupportOutput(device_name="switch-1").model_dump() == {
        "device_name": "switch-1",
        "redis_key": "",
        "download_url": "",
        "cl_support_log": "",
    }
    attachment = ticketing.UploadAttachmentInput(
        ticketing_platform="jira",
        issue_key="ABC-1",
        filename="diagnostics.txt",
        content=[0, 127, 255],  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
        content_type="text/plain",
    )
    assert attachment.content == b"\x00\x7f\xff"
    assert (
        ticketing.UploadAttachmentInput(
            ticketing_platform="jira",
            issue_key="ABC-1",
            filename="empty",
            content=[],  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
            content_type="application/octet-stream",
        ).content
        == b""
    )


def test_diagnostics_catalogs_and_legacy_paths_are_frozen() -> None:
    """Catalog order and supported legacy imports point at package-owned objects."""
    assert diagnostics.DIAGNOSTICS_ACTIVITIES == (
        diagnostics.run_diagnostic_commands,
        diagnostics.collect_tech_support_bundle,
    )
    assert ticketing.TICKETING_ACTIVITIES == (
        ticketing.validate_ticket,
        ticketing.upload_attachment,
        ticketing.upload_tech_support_from_redis,
        ticketing.add_ticket_comment,
    )
    for legacy, canonical in (
        (legacy_diagnostics, diagnostics),
        (legacy_ticketing, ticketing),
    ):
        for name in canonical.__all__:
            assert getattr(legacy, name) is getattr(canonical, name)
