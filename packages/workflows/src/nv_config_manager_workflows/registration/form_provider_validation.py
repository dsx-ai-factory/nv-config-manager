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
"""Resolve and strictly validate API-only workflow form option providers."""

import inspect
import types
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from importlib import import_module
from typing import Annotated, Literal, Union, cast, get_args, get_origin, get_type_hints

from pydantic import BaseModel, TypeAdapter, ValidationError

from nv_config_manager_workflows.registration.form_catalog import FormOptionProviderBinding
from nv_config_manager_workflows.ui import OptionSourceResponse, WorkflowFormContractError

type FormOptionResolver = Callable[[BaseModel], Awaitable[OptionSourceResponse]]


@dataclass(frozen=True, slots=True)
class ResolvedFormOptionProvider:
    """A catalog binding whose import targets and public query contract are valid."""

    binding: FormOptionProviderBinding
    resolver: FormOptionResolver
    query_model: type[BaseModel]


def resolve_form_option_provider(
    binding: FormOptionProviderBinding,
) -> ResolvedFormOptionProvider:
    """Import and validate one referenced provider without invoking its resolver."""
    where = f"{binding.workflow.__name__} form option provider {binding.source!r}"
    query_model = _load_reference(binding.declaration.query_model, f"{where} query_model")
    if not isinstance(query_model, type) or not issubclass(query_model, BaseModel):
        raise WorkflowFormContractError(
            f"{where} query_model must resolve to a Pydantic BaseModel class; "
            f"got {type(query_model).__name__}"
        )
    _validate_query_model(query_model, where)

    resolver = _load_reference(binding.declaration.resolver, f"{where} resolver")
    _validate_resolver(resolver, query_model, where)
    _validate_uses(binding, query_model, where)
    return ResolvedFormOptionProvider(
        binding=binding,
        resolver=cast(FormOptionResolver, resolver),
        query_model=query_model,
    )


def _load_reference(reference: str, where: str) -> object:
    module_name, attribute_path = reference.split(":", maxsplit=1)
    try:
        value: object = import_module(module_name)
        for attribute in attribute_path.split("."):
            value = getattr(value, attribute)
    except (ImportError, AttributeError) as error:
        raise WorkflowFormContractError(
            f"{where} {reference!r} could not be loaded: {type(error).__name__}: {error}"
        ) from error
    return value


def _validate_query_model(query_model: type[BaseModel], where: str) -> None:
    """Enforce the deliberately narrow, URL-safe v1 query-model contract."""
    if query_model.model_config.get("extra") != "forbid":
        raise WorkflowFormContractError(f"{where} query_model must set ConfigDict(extra='forbid')")

    schema_properties = query_model.model_json_schema().get("properties", {})
    for name, model_field in query_model.model_fields.items():
        aliases = (
            model_field.alias,
            model_field.validation_alias,
            model_field.serialization_alias,
        )
        if any(alias is not None for alias in aliases):
            raise WorkflowFormContractError(
                f"{where} query field {name!r} must not declare aliases"
            )
        if not _is_query_annotation(model_field.annotation):
            raise WorkflowFormContractError(
                f"{where} query field {name!r} must be a scalar, enum, literal, "
                "optional scalar, or list of scalars"
            )
        field_schema = schema_properties.get(name, {})
        if _schema_contains_secret_marker(field_schema):
            raise WorkflowFormContractError(
                f"{where} query field {name!r} must not be secret, password, or write-only"
            )


def _is_query_annotation(annotation: object, *, repeated: bool = False) -> bool:
    """Return whether an annotation has a flat URL-query representation."""
    origin = get_origin(annotation)
    if origin is Annotated:
        return _is_query_annotation(get_args(annotation)[0], repeated=repeated)
    if origin in (Union, types.UnionType):
        arguments = get_args(annotation)
        non_null = tuple(argument for argument in arguments if argument is not type(None))
        return (
            len(non_null) == 1
            and len(non_null) != len(arguments)
            and _is_query_annotation(non_null[0], repeated=repeated)
        )
    if origin is Literal:
        values = get_args(annotation)
        return bool(values) and all(_is_scalar_value(value) for value in values)
    if origin is list:
        arguments = get_args(annotation)
        return (
            not repeated
            and len(arguments) == 1
            and _is_query_annotation(arguments[0], repeated=True)
        )
    if origin is not None or not isinstance(annotation, type):
        return False
    if issubclass(annotation, Enum):
        return bool(tuple(annotation)) and all(
            _is_scalar_value(member.value) for member in annotation
        )
    return issubclass(annotation, (str, int, float, bool))


def _is_scalar_value(value: object) -> bool:
    return isinstance(value, (str, int, float, bool)) and not isinstance(value, bytes)


def _schema_contains_secret_marker(schema: object) -> bool:
    """Find password/write-only markers anywhere in one field's JSON Schema."""
    if isinstance(schema, Mapping):
        schema_mapping = cast(Mapping[str, object], schema)
        if schema_mapping.get("writeOnly") is True or schema_mapping.get("format") == "password":
            return True
        return any(_schema_contains_secret_marker(value) for value in schema_mapping.values())
    if isinstance(schema, list):
        return any(_schema_contains_secret_marker(value) for value in schema)
    return False


def _validate_resolver(
    resolver: object,
    query_model: type[BaseModel],
    where: str,
) -> None:
    """Require one plainly typed async callable for predictable invocation."""
    if not callable(resolver) or not inspect.iscoroutinefunction(resolver):
        raise WorkflowFormContractError(f"{where} resolver must be an async function")
    parameters = tuple(inspect.signature(resolver).parameters.values())
    if len(parameters) != 1 or parameters[0].kind not in (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    ):
        raise WorkflowFormContractError(
            f"{where} resolver must accept exactly one non-variadic query argument"
        )
    try:
        annotations = get_type_hints(resolver, include_extras=True)
    except (NameError, TypeError) as error:
        raise WorkflowFormContractError(
            f"{where} resolver annotations could not be resolved: {error}"
        ) from error
    parameter = parameters[0]
    if annotations.get(parameter.name) is not query_model:
        raise WorkflowFormContractError(
            f"{where} resolver argument must be annotated as {query_model.__name__}"
        )
    if annotations.get("return") is not OptionSourceResponse:
        raise WorkflowFormContractError(
            f"{where} resolver return must be annotated as OptionSourceResponse"
        )


def _validate_uses(
    binding: FormOptionProviderBinding,
    query_model: type[BaseModel],
    where: str,
) -> None:
    """Ensure every form use can supply the provider's declared query model."""
    fields = query_model.model_fields
    required = {name for name, model_field in fields.items() if model_field.is_required()}
    for position, use in enumerate(binding.uses, start=1):
        params = set(use.params)
        dependencies = set(use.depends_on)
        unknown = (params | dependencies) - set(fields)
        if unknown:
            raise WorkflowFormContractError(
                f"{where} use {position} supplies unknown query fields: {sorted(unknown)}"
            )
        overlap = params & dependencies
        if overlap:
            raise WorkflowFormContractError(
                f"{where} use {position} supplies query fields in both params and "
                f"depends_on: {sorted(overlap)}"
            )
        for name, value in use.params.items():
            _validate_static_param(fields[name].rebuild_annotation(), name, value, where, position)
        supplied_required = params | {
            name for name, dependency in use.depends_on.items() if dependency.required
        }
        missing = required - supplied_required
        if missing:
            raise WorkflowFormContractError(
                f"{where} use {position} does not supply required query fields: {sorted(missing)}"
            )


def _validate_static_param(
    annotation: object,
    name: str,
    value: object,
    where: str,
    position: int,
) -> None:
    """Validate one static value as FastAPI will assemble it from query parameters."""
    if isinstance(value, Sequence) and not isinstance(value, str | bytes) and not value:
        raise WorkflowFormContractError(
            f"{where} use {position} static query field {name!r} must not be empty"
        )
    candidate = (
        [value] if _is_repeated_annotation(annotation) and _is_scalar_value(value) else value
    )
    try:
        TypeAdapter(annotation).validate_python(candidate)
    except ValidationError as error:
        raise WorkflowFormContractError(
            f"{where} use {position} has an invalid static value for query field {name!r}"
        ) from error


def _is_repeated_annotation(annotation: object) -> bool:
    """Return whether FastAPI will collect repeated parameters for ``annotation``."""
    origin = get_origin(annotation)
    if origin is Annotated:
        return _is_repeated_annotation(get_args(annotation)[0])
    if origin in (Union, types.UnionType):
        non_null = tuple(
            argument for argument in get_args(annotation) if argument is not type(None)
        )
        return len(non_null) == 1 and _is_repeated_annotation(non_null[0])
    return origin is list


__all__ = [
    "FormOptionResolver",
    "ResolvedFormOptionProvider",
    "resolve_form_option_provider",
]
