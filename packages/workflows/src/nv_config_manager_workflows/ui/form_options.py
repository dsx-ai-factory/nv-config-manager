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
"""Plugin-facing declarations and normalized responses for form option providers.

The declarations in this module are intentionally inert. Importing a workflow
does not import its resolver or query-model module; the API process resolves
those references when it builds the form-option routes.
"""

from collections.abc import Mapping
from dataclasses import KW_ONLY, dataclass, field

from pydantic import BaseModel, ConfigDict, Field

from nv_config_manager_workflows.form_declarations import FormOptionProvider
from nv_config_manager_workflows.ui.option_sources import Dependency, OptionParamValue


@dataclass(frozen=True, slots=True)
class FormOptionSource:
    """A symbolic reference to an option provider owned by one workflow.

    Form-catalog construction replaces this authoring value with the existing
    browser-visible :class:`~nv_config_manager_workflows.ui.OptionSource`
    contract. It must never be serialized directly.
    """

    name: str
    _: KW_ONLY
    params: Mapping[str, OptionParamValue] = field(default_factory=dict)
    depends_on: Mapping[str, Dependency] = field(default_factory=dict)
    clear_on_change: bool = False


class OptionItem(BaseModel):
    """One normalized option rendered by a workflow form field."""

    model_config = ConfigDict(revalidate_instances="always")

    label: str
    value: str
    description: str | None = None
    group: str | None = None


class OptionSourceMeta(BaseModel):
    """Optional metadata accompanying normalized form options."""

    model_config = ConfigDict(revalidate_instances="always")

    matching_device_count: int | None = None
    warnings: list[str] = Field(default_factory=list)


class OptionSourceResponse(BaseModel):
    """Normalized response for a Python-declared direct option source."""

    model_config = ConfigDict(revalidate_instances="always")

    items: list[OptionItem]
    meta: OptionSourceMeta = Field(default_factory=OptionSourceMeta)


__all__ = [
    "FormOptionProvider",
    "FormOptionSource",
    "OptionItem",
    "OptionSourceMeta",
    "OptionSourceResponse",
]
