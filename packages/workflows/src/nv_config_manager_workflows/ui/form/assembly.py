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
"""Assemble a model-aware v1 workflow form.

An input model declares its form as ``rjsf_ui_schema: ClassVar[Mapping[str,
object]]``, a supported subset of an RJSF ``uiSchema``. :func:`build_form`
projects the model schema, validates the declaration, fills server-owned
options, and derives the capabilities the form requires.
"""

import copy
from collections.abc import Callable, Mapping
from typing import Any, cast

from pydantic import BaseModel, PydanticUserError

from nv_config_manager_workflows.ui.form.contract import (
    RJSF_UI_SCHEMA_ATTRIBUTE,
    UI_SCHEMA_VERSION,
    derive_requires,
)
from nv_config_manager_workflows.ui.form.errors import WorkflowFormContractError
from nv_config_manager_workflows.ui.form.option_providers import FormOptionSource
from nv_config_manager_workflows.ui.form.option_sources import OptionSource
from nv_config_manager_workflows.ui.form.projection import project_form_schema
from nv_config_manager_workflows.ui.form.validation import validate_ui_schema

type FormOptionSourceCompiler = Callable[[FormOptionSource], OptionSource]


def declared_ui_schema(
    model: type[BaseModel],
    *,
    compile_option_source: FormOptionSourceCompiler | None = None,
) -> dict[str, Any]:
    """Return a JSON-only deep copy of the model's ``rjsf_ui_schema``; ``{}`` when absent."""
    if RJSF_UI_SCHEMA_ATTRIBUTE in model.model_fields:
        raise WorkflowFormContractError(
            f"{model.__qualname__}.{RJSF_UI_SCHEMA_ATTRIBUTE} is a Pydantic model field; "
            f"declare form metadata as {RJSF_UI_SCHEMA_ATTRIBUTE}: "
            "ClassVar[Mapping[str, object]] instead"
        )
    declared = getattr(model, RJSF_UI_SCHEMA_ATTRIBUTE, None)
    if declared is None:
        return {}
    if not isinstance(declared, Mapping):
        raise WorkflowFormContractError(
            f"{model.__qualname__}.{RJSF_UI_SCHEMA_ATTRIBUTE} must be a mapping; got "
            f"{type(declared).__name__}"
        )
    return cast(
        dict[str, Any],
        _plain(
            declared,
            f"{model.__qualname__}.rjsf_ui_schema",
            compile_option_source=compile_option_source,
        ),
    )


def build_form(
    model: type[BaseModel] | None,
    *,
    compile_option_source: FormOptionSourceCompiler | None = None,
) -> dict[str, Any]:
    """Return a model-aware checked v1 form envelope for a workflow input model.

    Raises:
        WorkflowFormContractError: The declaration has an invalid model relationship.
    """
    if model is None:
        schema: dict[str, Any] = {}
        ui_schema: dict[str, Any] = {}
    else:
        try:
            schema = project_form_schema(model)
            ui_schema = declared_ui_schema(model, compile_option_source=compile_option_source)
            validate_ui_schema(model, schema, ui_schema)
        except WorkflowFormContractError:
            raise
        except (TypeError, KeyError, ValueError, PydanticUserError) as error:
            # A wrongly typed declaration value (an unhashable widget, say) or a
            # Pydantic JSON Schema error is a form problem too, so a third-party
            # plugin's form stays isolated.
            raise WorkflowFormContractError(
                f"{model.__qualname__}: invalid form declaration ({type(error).__name__}: {error})"
            ) from error
    return {
        "schema": schema,
        "ui_schema": ui_schema,
        "ui_schema_version": UI_SCHEMA_VERSION,
        "requires": derive_requires(ui_schema),
    }


def _plain(
    value: Any,
    where: str,
    *,
    compile_option_source: FormOptionSourceCompiler | None = None,
) -> Any:
    """Return a deep JSON copy of a declaration; reject non-JSON values."""
    if isinstance(value, FormOptionSource):
        if compile_option_source is None:
            raise WorkflowFormContractError(
                f"{where} holds unresolved FormOptionSource {value.name!r}; symbolic option "
                "sources require workflow provider context"
            )
        compiled = compile_option_source(value)
        if not isinstance(compiled, OptionSource):
            raise WorkflowFormContractError(
                f"{where} option-source compiler returned {type(compiled).__name__}, expected "
                "OptionSource"
            )
        return _plain(compiled.to_wire(), where, compile_option_source=compile_option_source)
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise WorkflowFormContractError(f"{where} has a non-string key")
        return {
            key: _plain(
                item,
                f"{where}.{key}",
                compile_option_source=compile_option_source,
            )
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [_plain(item, where, compile_option_source=compile_option_source) for item in value]
    if value is None or isinstance(value, str | int | float | bool):
        return copy.copy(value)
    raise WorkflowFormContractError(f"{where} holds a non-JSON value {value!r}")


__all__ = [
    "FormOptionSourceCompiler",
    "build_form",
    "declared_ui_schema",
]
