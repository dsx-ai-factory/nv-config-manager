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

import subprocess
import sys
from collections.abc import Sequence

import pytest
from pydantic import BaseModel
from temporalio import activity

from nv_config_manager_workflows.form_declarations import (
    FormOptionProvider as DeclaredFormOptionProvider,
)
from nv_config_manager_workflows.metadata import (
    RequiredActivity,
    WorkflowLockSpec,
    WorkflowMetadataMixin,
)
from nv_config_manager_workflows.ui import FormOptionProvider as PublicFormOptionProvider


class WorkflowInput(BaseModel):
    device: str


@activity.defn
async def collect_facts() -> None: ...


class DeviceBackupWorkflow(WorkflowMetadataMixin):
    """Back up one device."""

    workflow_name = "Device Backup"
    workflow_description = "Back up one device"
    workflow_input_class = WorkflowInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/backup"
    workflow_form_enabled = True
    workflow_form_id = "device-backup"
    workflow_group = "Configuration"
    workflow_required_activities = (collect_facts,)


def test_metadata_defaults_fail_closed() -> None:
    assert not WorkflowMetadataMixin.workflow_api_enabled
    assert not WorkflowMetadataMixin.workflow_mcp_enabled
    assert WorkflowMetadataMixin.workflow_api_endpoint is None
    assert WorkflowMetadataMixin.get_workflow_form_id() is None
    assert not WorkflowMetadataMixin.get_workflow_form_enabled()
    assert WorkflowMetadataMixin.get_workflow_form_option_providers() == {}
    assert WorkflowMetadataMixin.get_workflow_group() is None
    assert WorkflowMetadataMixin.get_workflow_required_activities() == ()


def test_importing_metadata_does_not_initialize_the_ui_package() -> None:
    script = "\n".join(
        [
            "import sys",
            "import nv_config_manager_workflows.metadata",
            (
                "assert not [name for name in sys.modules "
                "if name == 'nv_config_manager_workflows.ui' "
                "or name.startswith('nv_config_manager_workflows.ui.')], "
                "sorted(name for name in sys.modules "
                "if name.startswith('nv_config_manager_workflows.ui'))"
            ),
        ]
    )

    subprocess.run([sys.executable, "-c", script], check=True)


def test_public_form_option_provider_is_the_neutral_declaration() -> None:
    assert PublicFormOptionProvider is DeclaredFormOptionProvider


def test_required_activities_are_composed_across_the_mro() -> None:
    class Publisher:
        workflow_required_activities: Sequence[RequiredActivity] = (collect_facts,)

    class ArchivedWorkflow(WorkflowMetadataMixin, Publisher):
        workflow_required_activities: Sequence[RequiredActivity] = ("store_results",)

    assert ArchivedWorkflow.get_workflow_required_activities() == (
        collect_facts,
        "store_results",
    )


def test_metadata_accessors_read_subclass_declarations() -> None:
    assert DeviceBackupWorkflow.get_workflow_name() == "Device Backup"
    assert DeviceBackupWorkflow.get_workflow_description() == "Back up one device"
    assert DeviceBackupWorkflow.get_workflow_input_class() is WorkflowInput
    assert DeviceBackupWorkflow.get_workflow_api_enabled()
    assert DeviceBackupWorkflow.get_workflow_api_endpoint() == "/backup"
    assert DeviceBackupWorkflow.get_workflow_form_id() == "device-backup"
    assert DeviceBackupWorkflow.get_workflow_form_enabled()
    assert DeviceBackupWorkflow.get_workflow_group() == "Configuration"
    assert DeviceBackupWorkflow.get_workflow_required_activities() == (collect_facts,)
    assert DeviceBackupWorkflow.get_workflow_cli_name() == "device-backup"


def test_workflow_lock_accessor_reads_subclass_declaration() -> None:
    spec = WorkflowLockSpec(key_fields=["device"])

    class LockedWorkflow(WorkflowMetadataMixin):
        workflow_lock = spec

    assert LockedWorkflow.get_workflow_lock() is spec


def test_workflow_lock_is_absent_by_default() -> None:
    assert WorkflowMetadataMixin.get_workflow_lock() is None


def test_missing_name_is_reported_by_the_accessor() -> None:
    with pytest.raises(ValueError, match="missing workflow_name"):
        WorkflowMetadataMixin.get_workflow_name()


@pytest.mark.asyncio
async def test_input_canonicalization_is_identity_by_default() -> None:
    body = WorkflowInput(device="leaf-01")

    assert await DeviceBackupWorkflow.canonicalize_input(body) is body
