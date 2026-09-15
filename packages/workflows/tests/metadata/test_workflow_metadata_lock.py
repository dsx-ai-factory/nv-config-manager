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

import pytest
from pydantic import BaseModel, ValidationError

from nv_config_manager_workflows.metadata import WorkflowLockSpec, build_workflow_lock_key


class LockInput(BaseModel):
    site: str
    device: str | None = None


def test_lock_spec_requires_a_safe_renewal_interval() -> None:
    with pytest.raises(ValidationError, match="renewal buffer"):
        WorkflowLockSpec(key_fields=["site"], ttl_seconds=60, renew_interval_seconds=50)


def test_lock_key_uses_the_declared_scope_and_input_fields() -> None:
    spec = WorkflowLockSpec(
        key_fields=["site", "device"],
        namespace="ngc",
        include_workflow_name=True,
    )

    assert (
        build_workflow_lock_key(
            spec,
            workflow_name="DeployWorkflow",
            namespace="ignored",
            workflow_input=LockInput(site="rdu", device="leaf-01"),
        )
        == "wf-lock:ngc:DeployWorkflow:site=rdu:device=leaf-01"
    )


def test_lock_key_retains_the_legacy_unencoded_format() -> None:
    spec = WorkflowLockSpec(
        key_fields=["site"],
        namespace="fabric:one",
        include_workflow_name=True,
    )

    assert (
        build_workflow_lock_key(
            spec,
            workflow_name="Deploy:Workflow",
            namespace=None,
            workflow_input=LockInput(site="rdu/device 1"),
        )
        == "wf-lock:fabric:one:Deploy:Workflow:site=rdu/device 1"
    )


def test_lock_key_rejects_a_missing_input_value() -> None:
    spec = WorkflowLockSpec(key_fields=["device"])

    with pytest.raises(ValueError, match="device.*missing or empty"):
        build_workflow_lock_key(
            spec,
            workflow_name="DeployWorkflow",
            namespace=None,
            workflow_input=LockInput(site="rdu"),
        )
