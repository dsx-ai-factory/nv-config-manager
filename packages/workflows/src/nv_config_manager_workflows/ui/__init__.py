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
"""Workflow input forms: the v1 ``/form`` contract.

An input model declares its form as ``rjsf_ui_schema: ClassVar[Mapping[str,
object]]``, a validated subset of an RJSF ``uiSchema`` built with
:func:`api_options`, :func:`device_field`, :func:`location_field`, and
:func:`variant_rows`. Field markers (:class:`ServerOwned`,
:class:`FormExcluded`, :class:`FormSchema`) shape the projected form schema
without changing the model.
"""

from nv_config_manager_workflows.ui.errors import WorkflowFormContractError
from nv_config_manager_workflows.ui.form import (
    ENRICHED_API_OPTIONS_CAPABILITY,
    QUERY_ALIASES,
    QUERY_SEPARATORS,
    RJSF_UI_SCHEMA_ATTRIBUTE,
    UI_SCHEMA_VERSION,
    build_form,
    capability_manifest,
    supported_capabilities,
    wire_schema,
)
from nv_config_manager_workflows.ui.form_options import (
    FormOptionProvider,
    FormOptionSource,
    OptionItem,
    OptionSourceMeta,
    OptionSourceResponse,
)
from nv_config_manager_workflows.ui.form_schema import FormJsonSchema, project_form_schema
from nv_config_manager_workflows.ui.markers import FormExcluded, FormSchema, ServerOwned
from nv_config_manager_workflows.ui.option_sources import (
    Dependency,
    OptionResponse,
    OptionSource,
)
from nv_config_manager_workflows.ui.options import (
    OptionPresentation,
    api_options,
    device_field,
    location_field,
    variant_rows,
)

__all__ = [
    "ENRICHED_API_OPTIONS_CAPABILITY",
    "QUERY_ALIASES",
    "QUERY_SEPARATORS",
    "RJSF_UI_SCHEMA_ATTRIBUTE",
    "UI_SCHEMA_VERSION",
    "Dependency",
    "FormExcluded",
    "FormJsonSchema",
    "FormOptionProvider",
    "FormOptionSource",
    "FormSchema",
    "OptionPresentation",
    "OptionItem",
    "OptionResponse",
    "OptionSource",
    "OptionSourceMeta",
    "OptionSourceResponse",
    "ServerOwned",
    "WorkflowFormContractError",
    "api_options",
    "build_form",
    "capability_manifest",
    "device_field",
    "location_field",
    "project_form_schema",
    "supported_capabilities",
    "variant_rows",
    "wire_schema",
]
