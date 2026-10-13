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
"""Workflow-form assembly, projection, validation, and contract resources."""

from nv_config_manager_workflows.ui.form.assembly import (
    FormOptionSourceCompiler,
    build_form,
    declared_ui_schema,
)
from nv_config_manager_workflows.ui.form.contract import (
    ENRICHED_API_OPTIONS_CAPABILITY,
    EXCLUSIVE_GROUPS_CAPABILITY,
    FIELD_CAPABILITIES,
    FIELD_COMPARISON_CAPABILITY,
    HELP_VARIANT_CAPABILITY,
    HIDE_SCHEMA_DESCRIPTIONS_CAPABILITY,
    QUERY_SEPARATOR_CAPABILITY,
    RJSF_UI_SCHEMA_ATTRIBUTE,
    UI_SCHEMA_VERSION,
    Vocabulary,
    capability_manifest,
    derive_requires,
    supported_capabilities,
    vocabulary,
    wire_schema,
)
from nv_config_manager_workflows.ui.form.errors import WorkflowFormContractError
from nv_config_manager_workflows.ui.form.field_builders import (
    OptionPresentation,
    api_options,
    device_field,
    location_field,
    variant_rows,
)
from nv_config_manager_workflows.ui.form.markers import (
    FORM_MARKERS,
    UNSET,
    FormExcluded,
    FormSchema,
    ServerOwned,
)
from nv_config_manager_workflows.ui.form.option_providers import (
    FormOptionProvider,
    FormOptionSource,
    OptionItem,
    OptionSourceMeta,
    OptionSourceResponse,
)
from nv_config_manager_workflows.ui.form.option_sources import (
    Dependency,
    OptionResponse,
    OptionSource,
)
from nv_config_manager_workflows.ui.form.projection import (
    SUPPORTED_SERVER_OWNED_FIELDS,
    FormJsonSchema,
    field_marker,
    injectable_server_owned_fields,
    json_type,
    omitted_properties,
    project_form_schema,
)
from nv_config_manager_workflows.ui.form.projection_constraints import (
    validate_omitted_root_constraints,
)
from nv_config_manager_workflows.ui.form.validation import (
    QUERY_ALIASES,
    QUERY_SEPARATORS,
    validate_ui_schema,
)
from nv_config_manager_workflows.ui.form.wire_validation import validate_form_envelope

__all__ = [
    "ENRICHED_API_OPTIONS_CAPABILITY",
    "EXCLUSIVE_GROUPS_CAPABILITY",
    "FIELD_CAPABILITIES",
    "FIELD_COMPARISON_CAPABILITY",
    "FORM_MARKERS",
    "HELP_VARIANT_CAPABILITY",
    "HIDE_SCHEMA_DESCRIPTIONS_CAPABILITY",
    "QUERY_SEPARATOR_CAPABILITY",
    "QUERY_ALIASES",
    "QUERY_SEPARATORS",
    "RJSF_UI_SCHEMA_ATTRIBUTE",
    "SUPPORTED_SERVER_OWNED_FIELDS",
    "UI_SCHEMA_VERSION",
    "UNSET",
    "Dependency",
    "FormExcluded",
    "FormJsonSchema",
    "FormOptionProvider",
    "FormOptionSource",
    "FormOptionSourceCompiler",
    "FormSchema",
    "OptionItem",
    "OptionPresentation",
    "OptionResponse",
    "OptionSource",
    "OptionSourceMeta",
    "OptionSourceResponse",
    "ServerOwned",
    "Vocabulary",
    "WorkflowFormContractError",
    "api_options",
    "build_form",
    "capability_manifest",
    "declared_ui_schema",
    "derive_requires",
    "device_field",
    "field_marker",
    "injectable_server_owned_fields",
    "json_type",
    "location_field",
    "omitted_properties",
    "project_form_schema",
    "supported_capabilities",
    "validate_form_envelope",
    "validate_omitted_root_constraints",
    "validate_ui_schema",
    "vocabulary",
    "variant_rows",
    "wire_schema",
]
