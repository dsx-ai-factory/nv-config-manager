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
"""Freeze the Temporal type names registered by the default worker.

This test ensures the default worker's registered Temporal type names remain
unchanged during the package move. It will be removed once the package move is complete.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict, cast

from nv_config_manager.temporal.common.activities import REGISTERED_COMMON_ACTIVITIES
from nv_config_manager.temporal.hello_world.activities import (
    REGISTERED_ACTIVITIES as HELLO_WORLD_ACTIVITIES,
)
from nv_config_manager.temporal.hello_world.workflows import (
    REGISTERED_WORKFLOWS as HELLO_WORLD_WORKFLOWS,
)
from nv_config_manager.temporal.ngc.activities import REGISTERED_ACTIVITIES as NGC_ACTIVITIES
from nv_config_manager.temporal.ngc.workflows import REGISTERED_WORKFLOWS as NGC_WORKFLOWS
from nv_config_manager_workflows.metadata import WorkflowMetadataMixin
from nv_config_manager_workflows.registration import activity_name, workflow_type_name

TYPE_NAME_SNAPSHOT = Path(__file__).parent / "data" / "registered_temporal_type_names.json"


class TypeNameSnapshot(TypedDict):
    """Temporal workflow and activity names captured before the package move."""

    workflows: list[str]
    activities: list[str]


def test_registered_temporal_type_names_are_unchanged_by_the_move() -> None:
    """The move must preserve every name recorded in existing workflow histories."""
    expected: TypeNameSnapshot = json.loads(TYPE_NAME_SNAPSHOT.read_text())

    workflow_names: list[str] = []
    for workflow in (*NGC_WORKFLOWS, *HELLO_WORLD_WORKFLOWS):
        name = workflow_type_name(cast(type[WorkflowMetadataMixin], workflow))
        assert name is not None, f"{workflow.__name__} declares no workflow type name"
        workflow_names.append(name)

    activity_names: list[str] = []
    for registered_activity in (
        *NGC_ACTIVITIES,
        *HELLO_WORLD_ACTIVITIES,
        *REGISTERED_COMMON_ACTIVITIES,
    ):
        callable_activity = cast(Callable[..., Any], registered_activity)
        name = activity_name(callable_activity)
        label = getattr(callable_activity, "__qualname__", None) or repr(callable_activity)
        assert name is not None, f"{label} declares no activity name"
        activity_names.append(name)

    assert sorted(workflow_names) == expected["workflows"]
    assert sorted(activity_names) == expected["activities"]
