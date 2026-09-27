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
"""Freeze Temporal contracts before extracting the workflow lock chain."""

import json
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

from nv_config_manager.temporal import converter as legacy_converter
from nv_config_manager.temporal.common.activities import REGISTERED_COMMON_ACTIVITIES
from nv_config_manager.temporal.common.activities import lock as legacy_lock_activities
from nv_config_manager.temporal.common.decorators import workflow as legacy_workflow_decorator
from nv_config_manager.temporal.hello_world.activities import (
    REGISTERED_ACTIVITIES as HELLO_WORLD_ACTIVITIES,
)
from nv_config_manager.temporal.hello_world.activities import (
    hello_world as legacy_hello_world_activities,
)
from nv_config_manager.temporal.hello_world.workflows import (
    LOCAL_TEST_WORKFLOWS as HELLO_WORLD_LOCAL_TEST_WORKFLOWS,
)
from nv_config_manager.temporal.hello_world.workflows import (
    REGISTERED_WORKFLOWS as HELLO_WORLD_WORKFLOWS,
)
from nv_config_manager.temporal.ngc.activities import REGISTERED_ACTIVITIES as NGC_ACTIVITIES
from nv_config_manager.temporal.ngc.activities import backup as legacy_backup_activities
from nv_config_manager.temporal.ngc.activities import bmc as legacy_bmc_activities
from nv_config_manager.temporal.ngc.activities import (
    cable_validation as legacy_cable_validation_activities,
)
from nv_config_manager.temporal.ngc.activities import config as legacy_config_activities
from nv_config_manager.temporal.ngc.activities import dcim as legacy_dcim_activities
from nv_config_manager.temporal.ngc.activities import deploy as legacy_deploy_activities
from nv_config_manager.temporal.ngc.activities import device as legacy_device_activities
from nv_config_manager.temporal.ngc.activities import (
    device_password_rotation as legacy_device_password_rotation_activities,
)
from nv_config_manager.temporal.ngc.activities import (
    diagnostics as legacy_diagnostics_activities,
)
from nv_config_manager.temporal.ngc.activities import (
    hardware_validation as legacy_hardware_validation_activities,
)
from nv_config_manager.temporal.ngc.activities import (
    ib_guid_discovery as legacy_ib_guid_discovery_activities,
)
from nv_config_manager.temporal.ngc.activities import ib_pkey as legacy_ib_pkey_activities
from nv_config_manager.temporal.ngc.activities import nats as legacy_nats_activities
from nv_config_manager.temporal.ngc.activities import nautobot as legacy_nautobot_activities
from nv_config_manager.temporal.ngc.activities import (
    nvlinkswitch_firmware as legacy_nvlinkswitch_firmware_activities,
)
from nv_config_manager.temporal.ngc.activities import os as legacy_os_activities
from nv_config_manager.temporal.ngc.activities import render as legacy_render_activities
from nv_config_manager.temporal.ngc.activities import slack as legacy_slack_activities
from nv_config_manager.temporal.ngc.activities import ticketing as legacy_ticketing_activities
from nv_config_manager.temporal.ngc.activities import ufm as legacy_ufm_activities
from nv_config_manager.temporal.ngc.workflows import REGISTERED_WORKFLOWS as NGC_WORKFLOWS
from nv_config_manager_workflows import converter as canonical_converter
from nv_config_manager_workflows.activities import backup as canonical_backup_activities
from nv_config_manager_workflows.activities import bmc as canonical_bmc_activities
from nv_config_manager_workflows.activities import (
    cable_validation as canonical_cable_validation_activities,
)
from nv_config_manager_workflows.activities import config as canonical_config_activities
from nv_config_manager_workflows.activities import dcim as canonical_dcim_activities
from nv_config_manager_workflows.activities import deploy as canonical_deploy_activities
from nv_config_manager_workflows.activities import device as canonical_device_activities
from nv_config_manager_workflows.activities import (
    device_password_rotation as canonical_device_password_rotation_activities,
)
from nv_config_manager_workflows.activities import (
    diagnostics as canonical_diagnostics_activities,
)
from nv_config_manager_workflows.activities import (
    hardware_validation as canonical_hardware_validation_activities,
)
from nv_config_manager_workflows.activities import hello_world as canonical_hello_world_activities
from nv_config_manager_workflows.activities import (
    ib_guid_discovery as canonical_ib_guid_discovery_activities,
)
from nv_config_manager_workflows.activities import ib_pkey as canonical_ib_pkey_activities
from nv_config_manager_workflows.activities import lock as canonical_lock_activities
from nv_config_manager_workflows.activities import nats as canonical_nats_activities
from nv_config_manager_workflows.activities import (
    nvlinkswitch_firmware as canonical_nvlinkswitch_firmware_activities,
)
from nv_config_manager_workflows.activities import os as canonical_os_activities
from nv_config_manager_workflows.activities import render as canonical_render_activities
from nv_config_manager_workflows.activities import slack as canonical_slack_activities
from nv_config_manager_workflows.activities import ticketing as canonical_ticketing_activities
from nv_config_manager_workflows.activities import ufm as canonical_ufm_activities
from nv_config_manager_workflows.decorators import workflow as canonical_workflow_decorator
from nv_config_manager_workflows.metadata import WorkflowMetadataMixin
from nv_config_manager_workflows.metadata import lock as workflow_lock
from nv_config_manager_workflows.registration.contract import activity_name, workflow_type_name

_FIXTURES = Path(__file__).with_name("fixtures")
_REGISTERED_ACTIVITIES = [
    *NGC_ACTIVITIES,
    *HELLO_WORLD_ACTIVITIES,
    *REGISTERED_COMMON_ACTIVITIES,
]
_REGISTERED_WORKFLOWS = [
    *NGC_WORKFLOWS,
    *HELLO_WORLD_WORKFLOWS,
    *HELLO_WORLD_LOCAL_TEST_WORKFLOWS,
]


def _required_name(name: str | None) -> str:
    """Reject dynamic or undecorated entries in an explicit worker registration list."""
    assert name is not None
    return name


def _registered_type_names() -> dict[str, list[str]]:
    """Read the type names Temporal sees from every worker registration source."""
    return {
        "activities": sorted(
            _required_name(activity_name(cast(Callable[..., Any], registered)))
            for registered in _REGISTERED_ACTIVITIES
        ),
        "workflows": sorted(
            _required_name(workflow_type_name(cast(type[WorkflowMetadataMixin], registered)))
            for registered in _REGISTERED_WORKFLOWS
        ),
    }


def test_history_bearing_identifiers_are_frozen_for_gnicfd_6327() -> None:
    """GNICFD-6327 extraction must not change identifiers recorded in histories or Redis."""
    assert canonical_converter.COMPRESSION_ENCODING == "binary/gzip"
    assert workflow_lock._LOCK_KEY_PREFIX == "wf-lock"
    assert canonical_workflow_decorator._WORKFLOW_LOCK_PATCH_ID == "nvcm-workflow-lock-v1"


def test_registered_temporal_type_names_are_frozen_for_gnicfd_6327() -> None:
    """GNICFD-6327 extraction must not rename or duplicate registered Temporal types."""
    expected: dict[str, list[str]] = json.loads(
        (_FIXTURES / "registered_type_names.json").read_text()
    )
    actual = _registered_type_names()

    assert actual == expected
    assert len(actual["activities"]) == len(set(actual["activities"]))
    assert len(actual["workflows"]) == len(set(actual["workflows"]))


def test_lock_activity_compatibility_path_exports_canonical_objects() -> None:
    """The old path preserves object identity without decorating a second activity set."""
    exported_names = (
        "AcquireWorkflowLockInput",
        "RenewWorkflowLockInput",
        "ReleaseWorkflowLockInput",
        "acquire_workflow_lock",
        "renew_workflow_lock",
        "release_workflow_lock",
    )

    for name in exported_names:
        assert getattr(legacy_lock_activities, name) is getattr(canonical_lock_activities, name)


def test_core_activity_compatibility_paths_export_canonical_objects() -> None:
    """Core service modules remain aliases rather than redecorated copies."""
    module_exports = (
        (
            legacy_hello_world_activities,
            canonical_hello_world_activities,
            (
                "HELLO_WORLD_ACTIVITIES",
                "hello_world_activity",
                "hello_world_prompt_activity",
                "hello_world_reject_activity",
            ),
        ),
        (
            legacy_config_activities,
            canonical_config_activities,
            ("CONFIG_ACTIVITIES", "build_workflow_url", "get_ui_base_url"),
        ),
        (
            legacy_nats_activities,
            canonical_nats_activities,
            ("ARCHIVE_SUBJECT", "NATS_ACTIVITIES", "PublishNatsInput", "publish_nats"),
        ),
        (
            legacy_slack_activities,
            canonical_slack_activities,
            (
                "SLACK_ACTIVITIES",
                "SlackMessageInput",
                "SlackMessageOutput",
                "send_slack_message",
            ),
        ),
    )

    for legacy_module, canonical_module, exported_names in module_exports:
        for name in exported_names:
            assert getattr(legacy_module, name) is getattr(canonical_module, name)


def test_backup_and_deploy_compatibility_paths_export_canonical_objects() -> None:
    """Moved deployment paths expose exact package-owned models and activities."""
    for legacy_module, canonical_module in (
        (legacy_backup_activities, canonical_backup_activities),
        (legacy_deploy_activities, canonical_deploy_activities),
    ):
        assert legacy_module.__all__ == canonical_module.__all__
        for name in canonical_module.__all__:
            assert getattr(legacy_module, name) is getattr(canonical_module, name)


def test_render_compatibility_path_exports_canonical_objects() -> None:
    """The moved Render path exposes exact package-owned models and activities."""
    assert legacy_render_activities.__all__ == canonical_render_activities.__all__
    for name in canonical_render_activities.__all__:
        assert getattr(legacy_render_activities, name) is getattr(canonical_render_activities, name)


def test_os_compatibility_path_exports_canonical_objects() -> None:
    """The moved OS path exposes exact package-owned models and activities."""
    assert legacy_os_activities.__all__ == canonical_os_activities.__all__
    for name in canonical_os_activities.__all__:
        assert getattr(legacy_os_activities, name) is getattr(canonical_os_activities, name)


def test_nvlink_compatibility_path_exports_canonical_objects() -> None:
    """The moved NVLink path exposes exact package-owned models and activities."""
    assert (
        legacy_nvlinkswitch_firmware_activities.__all__
        == canonical_nvlinkswitch_firmware_activities.__all__
    )
    for name in canonical_nvlinkswitch_firmware_activities.__all__:
        assert getattr(legacy_nvlinkswitch_firmware_activities, name) is getattr(
            canonical_nvlinkswitch_firmware_activities, name
        )


@pytest.mark.parametrize(
    ("legacy_module", "canonical_module"),
    [
        pytest.param(
            legacy_cable_validation_activities,
            canonical_cable_validation_activities,
            id="cable-validation",
        ),
        pytest.param(
            legacy_hardware_validation_activities,
            canonical_hardware_validation_activities,
            id="hardware-validation",
        ),
        pytest.param(
            legacy_device_password_rotation_activities,
            canonical_device_password_rotation_activities,
            id="device-password-rotation",
        ),
        pytest.param(legacy_bmc_activities, canonical_bmc_activities, id="bmc"),
    ],
)
def test_device_operation_compatibility_paths_export_canonical_objects(
    legacy_module: ModuleType,
    canonical_module: ModuleType,
) -> None:
    """Moved device-operation paths expose exact package-owned supported objects."""
    assert legacy_module.__all__ == canonical_module.__all__
    for name in canonical_module.__all__:
        assert getattr(legacy_module, name) is getattr(canonical_module, name)


def test_diagnostics_compatibility_paths_export_canonical_objects() -> None:
    """Moved diagnostics paths expose exact package-owned supported objects."""
    for legacy_module, canonical_module in (
        (legacy_diagnostics_activities, canonical_diagnostics_activities),
        (legacy_ticketing_activities, canonical_ticketing_activities),
    ):
        assert legacy_module.__all__ == canonical_module.__all__
        for name in canonical_module.__all__:
            assert getattr(legacy_module, name) is getattr(canonical_module, name)


def test_activity_facades_do_not_bind_private_implementation_names() -> None:
    """Compatibility facades expose supported objects, not package-private helpers."""
    for legacy_module in (
        legacy_backup_activities,
        legacy_bmc_activities,
        legacy_cable_validation_activities,
        legacy_config_activities,
        legacy_dcim_activities,
        legacy_deploy_activities,
        legacy_device_activities,
        legacy_hardware_validation_activities,
        legacy_device_password_rotation_activities,
        legacy_diagnostics_activities,
        legacy_ib_guid_discovery_activities,
        legacy_ib_pkey_activities,
        legacy_nats_activities,
        legacy_nautobot_activities,
        legacy_nvlinkswitch_firmware_activities,
        legacy_os_activities,
        legacy_render_activities,
        legacy_slack_activities,
        legacy_ticketing_activities,
        legacy_ufm_activities,
    ):
        private_names = {
            name
            for name in vars(legacy_module)
            if name.startswith("_") and not name.startswith("__")
        }
        assert private_names == set()


def test_dcim_activity_compatibility_paths_export_canonical_objects() -> None:
    """Legacy DCIM and Nautobot paths expose every package-owned DCIM activity."""
    exported_names = set(canonical_dcim_activities.__all__) - {"dcim_client_session"}

    for name in exported_names:
        canonical = getattr(canonical_dcim_activities, name)
        assert getattr(legacy_dcim_activities, name) is canonical
        assert getattr(legacy_nautobot_activities, name) is canonical

    assert legacy_nautobot_activities is legacy_dcim_activities


def test_device_activity_compatibility_path_exports_canonical_objects() -> None:
    """The legacy device path exposes package-owned models and activities."""
    for name in canonical_device_activities.__all__:
        assert getattr(legacy_device_activities, name) is getattr(canonical_device_activities, name)


def test_ufm_activity_compatibility_path_exports_canonical_objects() -> None:
    """The legacy UFM path exposes package-owned models and activities."""
    for name in canonical_ufm_activities.__all__:
        assert getattr(legacy_ufm_activities, name) is getattr(canonical_ufm_activities, name)


def test_ib_pkey_activity_compatibility_path_exports_canonical_objects() -> None:
    """The legacy PKey path exposes package-owned models, constants, and activities."""
    for name in canonical_ib_pkey_activities.__all__:
        assert getattr(legacy_ib_pkey_activities, name) is getattr(
            canonical_ib_pkey_activities, name
        )


def test_ib_guid_discovery_compatibility_path_exports_canonical_objects() -> None:
    """The legacy IB GUID path exposes package-owned models and activities."""
    for name in canonical_ib_guid_discovery_activities.__all__:
        assert getattr(legacy_ib_guid_discovery_activities, name) is getattr(
            canonical_ib_guid_discovery_activities, name
        )


def test_workflow_decorator_compatibility_path_exports_canonical_objects() -> None:
    """The old decorator path preserves object identity without defining another workflow run."""
    assert (
        legacy_workflow_decorator.run_nv_config_manager_workflow
        is canonical_workflow_decorator.run_nv_config_manager_workflow
    )
    assert (
        legacy_workflow_decorator.WorkflowRuntimeFailure
        is canonical_workflow_decorator.WorkflowRuntimeFailure
    )


def test_converter_compatibility_path_exports_canonical_objects() -> None:
    """The old converter path exposes the exact canonical codec and factory objects."""
    assert legacy_converter.COMPRESSION_ENCODING == canonical_converter.COMPRESSION_ENCODING
    assert legacy_converter.CompressionPayloadCodec is canonical_converter.CompressionPayloadCodec
    assert legacy_converter.get_data_converter is canonical_converter.get_data_converter
