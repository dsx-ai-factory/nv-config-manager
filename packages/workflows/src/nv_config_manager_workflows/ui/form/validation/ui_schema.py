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
"""Orchestration for model-aware workflow-form UI-schema validation."""

from typing import Any

from pydantic import BaseModel

from nv_config_manager_workflows.ui.form.validation.context import FormValidationContext
from nv_config_manager_workflows.ui.form.validation.fields import (
    check_core_field,
    check_field,
    check_root,
)
from nv_config_manager_workflows.ui.form.validation.relationships import (
    check_cycles,
    check_exclusive_groups,
    check_field_comparisons,
    check_hidden,
    check_scopes,
    check_url_owners,
)


def validate_ui_schema(
    model: type[BaseModel], schema: dict[str, Any], ui_schema: dict[str, Any]
) -> None:
    """Validate and complete one model's UI schema against its projected JSON Schema."""
    context = FormValidationContext(model, schema, ui_schema)
    for key, value in context.ui.items():
        if key.startswith("ui:"):
            check_root(context, key, value)
        else:
            context.require_property(key, f"key {key!r}", wire_name=True)
            if not isinstance(value, dict):
                raise context.fail(f"{key!r} must be a mapping; got {value!r}")
            context.fields[key] = value
            check_field(context, key, value)
    for name, entry in context.fields.items():
        check_core_field(context, name, entry)
    check_hidden(context)
    check_scopes(context)
    check_exclusive_groups(context)
    check_field_comparisons(context)
    check_cycles(context)
    check_url_owners(context)


__all__ = ["validate_ui_schema"]
