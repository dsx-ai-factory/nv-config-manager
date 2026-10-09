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
"""Validate assembled workflow forms against the canonical wire contract."""

from collections.abc import Mapping
from functools import cache
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from nv_config_manager_workflows.ui.errors import WorkflowFormContractError
from nv_config_manager_workflows.ui.form import wire_schema


@cache
def _wire_validator() -> Draft202012Validator:
    """Compile the packaged contract once per process."""
    schema = wire_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _error_sort_key(error: ValidationError) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return a stable key even when a path contains both property names and indexes."""
    return (
        tuple(map(str, error.absolute_path)),
        tuple(map(str, error.absolute_schema_path)),
    )


def _error_path(error: ValidationError) -> str:
    """Render a concise JSON path for a contract diagnostic."""
    path = "$"
    for part in error.absolute_path:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return path


def validate_form_envelope(envelope: Mapping[str, Any]) -> None:
    """Raise when an assembled form violates the canonical v1 wire schema."""
    errors = sorted(_wire_validator().iter_errors(envelope), key=_error_sort_key)
    if errors:
        error = errors[0]
        raise WorkflowFormContractError(f"form envelope {_error_path(error)}: {error.message}")


__all__ = ["validate_form_envelope"]
