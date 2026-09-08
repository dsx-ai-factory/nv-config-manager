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
"""Compatibility tests for provider-neutral Temporal identifiers."""

from unittest.mock import patch

import pytest
from temporalio import activity

from nv_config_manager.temporal.ngc.activities.ib_dcim import (
    create_partition_in_dcim,
    create_partition_in_nautobot,
    record_ib_pkey_in_dcim,
    record_ib_pkey_in_nautobot,
)
from nv_config_manager.temporal.ngc.workflows.bmc import RedfishProvisioningWorkflow
from nv_config_manager.temporal.ngc.workflows.ib_pkey_creation import IBPKeyCreationWorkflow
from nv_config_manager.temporal.ngc.workflows.ib_pkey_member_update import (
    IBPKeyMemberUpdateWorkflow,
)


@pytest.mark.parametrize(
    ("workflow_class", "neutral_name", "legacy_name"),
    [
        (RedfishProvisioningWorkflow, "write_to_dcim", "write_to_nautobot"),
        (IBPKeyCreationWorkflow, "record_dcim", "record_nautobot"),
        (IBPKeyMemberUpdateWorkflow, "update_dcim", "update_nautobot"),
    ],
)
@pytest.mark.parametrize("patch_enabled", [True, False])
def test_dcim_stage_identifiers_follow_temporal_patch(
    workflow_class: type,
    neutral_name: str,
    legacy_name: str,
    patch_enabled: bool,
) -> None:
    """New histories use neutral names while old histories retain their stage IDs."""
    with (
        patch("temporalio.workflow.patched", return_value=patch_enabled),
        patch("temporalio.workflow.time", return_value=0.0),
    ):
        stage_names = {stage.name for stage in workflow_class()._stages}

    assert (neutral_name in stage_names) is patch_enabled
    assert (legacy_name in stage_names) is not patch_enabled


@pytest.mark.parametrize(
    ("activity_callable", "expected_name"),
    [
        (create_partition_in_dcim, "create_partition_in_dcim"),
        (create_partition_in_nautobot, "create_partition_in_nautobot"),
        (record_ib_pkey_in_dcim, "record_ib_pkey_in_dcim"),
        (record_ib_pkey_in_nautobot, "record_ib_pkey_in_nautobot"),
    ],
)
def test_dcim_and_legacy_activity_names_are_registered(
    activity_callable: object,
    expected_name: str,
) -> None:
    """Workers can service both sides of the workflow history transition."""
    definition = activity._Definition.must_from_callable(activity_callable)

    assert definition.name == expected_name
