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
"""Canonical wire validation of completed workflow form envelopes."""

from collections.abc import Mapping
from typing import ClassVar

import pytest
from pydantic import BaseModel

from nv_config_manager_workflows.ui import WorkflowFormContractError, build_form
from nv_config_manager_workflows.ui.wire_validation import validate_form_envelope


class NullWidgetInput(BaseModel):
    rjsf_ui_schema: ClassVar[Mapping[str, object]] = {"name": {"ui:widget": None}}

    name: str


class NullExclusiveGroupsInput(BaseModel):
    rjsf_ui_schema: ClassVar[Mapping[str, object]] = {"ui:globalOptions": {"exclusiveGroups": None}}

    name: str


class EmptyDeviceFiltersInput(BaseModel):
    rjsf_ui_schema: ClassVar[Mapping[str, object]] = {
        "ui:globalOptions": {
            "exclusiveGroups": [
                {"fields": ["first"], "deviceFilters": []},
                {"fields": ["second"]},
            ]
        }
    }

    first: str | None = None
    second: str | None = None


@pytest.mark.parametrize(
    ("model", "path"),
    [
        (NullWidgetInput, "$.ui_schema.name.ui:widget"),
        (NullExclusiveGroupsInput, "$.ui_schema.ui:globalOptions.exclusiveGroups"),
        (
            EmptyDeviceFiltersInput,
            "$.ui_schema.ui:globalOptions.exclusiveGroups[0].deviceFilters",
        ),
    ],
)
def test_wire_validation_rejects_declarations_the_semantic_checker_accepts(
    model: type[BaseModel], path: str
) -> None:
    envelope = build_form(model)

    with pytest.raises(WorkflowFormContractError) as raised:
        validate_form_envelope(envelope)

    assert f"{path}:" in str(raised.value)
