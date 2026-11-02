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
"""Shared state and schema helpers for workflow-form semantic validation."""

from typing import Any

from pydantic import BaseModel

from nv_config_manager_workflows.ui.form.contract import (
    RJSF_UI_SCHEMA_ATTRIBUTE,
    Vocabulary,
    vocabulary,
)
from nv_config_manager_workflows.ui.form.errors import WorkflowFormContractError
from nv_config_manager_workflows.ui.form.projection import json_type, omitted_properties


class FormValidationContext:
    """Mutable state shared by one UI-schema validation pass."""

    def __init__(
        self, model: type[BaseModel], schema: dict[str, Any], ui_schema: dict[str, Any]
    ) -> None:
        self.model = model
        self.name = model.__qualname__
        self.key = f"{model.__module__}.{model.__qualname__}"
        self.properties: dict[str, Any] = schema.get("properties", {})
        self.definitions: dict[str, Any] = schema.get("$defs", {})
        self.omitted = omitted_properties(model)
        self.ui = ui_schema
        self.vocabulary: Vocabulary = vocabulary()
        self.fields: dict[str, dict[str, Any]] = {}
        self.dependencies: dict[str, list[str]] = {}
        self.composite_owners: dict[str, str] = {}

    def fail(self, message: str) -> WorkflowFormContractError:
        """Return a contract error scoped to the model's UI-schema declaration."""
        return WorkflowFormContractError(f"{self.name}.{RJSF_UI_SCHEMA_ATTRIBUTE}: {message}")

    def require_property(self, name: object, where: str, *, wire_name: bool = False) -> str:
        """Require a projected property, optionally with a wire-compatible name."""
        if name in self.omitted:
            raise self.fail(
                f"{where} names {name!r}, which is ServerOwned or FormExcluded; omitted "
                "fields are not part of the form and must not appear in its declaration"
            )
        if not isinstance(name, str) or name not in self.properties:
            raise self.fail(
                f"{where} names {name!r}, which is not a property of the form schema; known "
                f"properties: {', '.join(self.properties) or '(none)'}"
            )
        if wire_name and not self.vocabulary.property_name.fullmatch(name):
            raise self.fail(
                f"{where} names {name!r}, which does not match the v1 property name pattern "
                f"{self.vocabulary.property_name.pattern!r}; give the field a plain alias"
            )
        return name

    def resolve_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        """Return a local schema after following its definition reference."""
        current = schema
        seen: set[str] = set()
        while "$ref" in current:
            name = current["$ref"].rsplit("/", 1)[-1]
            if name in seen or name not in self.definitions:
                break
            seen.add(name)
            current = self.definitions[name]
        return current

    def is_string_or_strings(self, prop: dict[str, Any]) -> bool:
        """Return whether a property is a string or an array of strings."""
        resolved = self.resolve_schema(prop)
        kind = json_type(resolved, self.definitions)
        if kind == "array":
            return json_type(resolved.get("items", {}), self.definitions) == "string"
        return kind == "string"


__all__ = ["FormValidationContext"]
