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
from pathlib import Path

from nv_config_manager.temporal import converter as legacy_converter
from nv_config_manager.temporal.common.activities import REGISTERED_COMMON_ACTIVITIES
from nv_config_manager.temporal.common.activities import lock as legacy_lock_activities
from nv_config_manager.temporal.common.decorators import workflow as legacy_workflow_decorator
from nv_config_manager.temporal.hello_world.activities import (
    REGISTERED_ACTIVITIES as HELLO_WORLD_ACTIVITIES,
)
from nv_config_manager.temporal.hello_world.workflows import (
    LOCAL_TEST_WORKFLOWS as HELLO_WORLD_LOCAL_TEST_WORKFLOWS,
)
from nv_config_manager.temporal.hello_world.workflows import (
    REGISTERED_WORKFLOWS as HELLO_WORLD_WORKFLOWS,
)
from nv_config_manager.temporal.ngc.activities import REGISTERED_ACTIVITIES as NGC_ACTIVITIES
from nv_config_manager.temporal.ngc.workflows import REGISTERED_WORKFLOWS as NGC_WORKFLOWS
from nv_config_manager_workflows import converter as canonical_converter
from nv_config_manager_workflows.activities import lock as canonical_lock_activities
from nv_config_manager_workflows.decorators import workflow as canonical_workflow_decorator
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
            _required_name(activity_name(registered)) for registered in _REGISTERED_ACTIVITIES
        ),
        "workflows": sorted(
            _required_name(workflow_type_name(registered)) for registered in _REGISTERED_WORKFLOWS
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
