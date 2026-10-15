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
"""Validate root JSON Schema constraints before form properties are omitted."""

import re
from typing import Any, Never, cast
from urllib.parse import unquote

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import SchemaError  # type: ignore[import-untyped]
from pydantic import BaseModel

from nv_config_manager_workflows.ui.form.errors import WorkflowFormContractError

_SAME_INSTANCE_ARRAY_APPLICATORS = ("allOf", "anyOf", "oneOf")
_WHOLE_OBJECT_ASSERTIONS = ("minProperties", "maxProperties", "const", "enum")
_UNSUPPORTED_ROOT_KEYWORDS = (
    "$anchor",
    "$dynamicAnchor",
    "$dynamicRef",
    "$recursiveAnchor",
    "$recursiveRef",
    "$vocabulary",
)
_SPECIAL_ROOT_KEYWORDS = frozenset(
    {
        "$defs",
        "$id",
        "$ref",
        "$schema",
        "default",
        "definitions",
        "dependencies",
        "dependentRequired",
        "dependentSchemas",
        "else",
        "if",
        "not",
        "properties",
        "required",
        "then",
    }
)
_PASS_THROUGH_ROOT_KEYWORDS = frozenset(
    {
        "$comment",
        "additionalProperties",
        "contains",
        "contentEncoding",
        "contentMediaType",
        "contentSchema",
        "deprecated",
        "description",
        "discriminator",
        "examples",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "format",
        "items",
        "maxContains",
        "maxItems",
        "maxLength",
        "maximum",
        "minContains",
        "minItems",
        "minLength",
        "minimum",
        "multipleOf",
        "pattern",
        "patternProperties",
        "prefixItems",
        "propertyNames",
        "readOnly",
        "title",
        "type",
        "unevaluatedItems",
        "unevaluatedProperties",
        "uniqueItems",
        "writeOnly",
    }
)
_ROOT_KEYWORD_GROUPS = (
    _SAME_INSTANCE_ARRAY_APPLICATORS,
    _WHOLE_OBJECT_ASSERTIONS,
    _UNSUPPORTED_ROOT_KEYWORDS,
    _SPECIAL_ROOT_KEYWORDS,
    _PASS_THROUGH_ROOT_KEYWORDS,
)
_KNOWN_ROOT_KEYWORDS = frozenset().union(*_ROOT_KEYWORD_GROUPS)
_DRAFT_2020_12_SCHEMA_URI = "https://json-schema.org/draft/2020-12/schema"
_INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_ARRAY_INDEX = re.compile(r"0|[1-9][0-9]*")


def _json_pointer(path: tuple[str, ...]) -> str:
    """Return a JSON Pointer for one path in the generated schema."""
    if not path:
        return "#"
    escaped = (part.replace("~", "~0").replace("/", "~1") for part in path)
    return f"#/{'/'.join(escaped)}"


def _local_ref_target(
    root: dict[str, Any], reference: str
) -> tuple[object, tuple[str, ...]] | None:
    """Resolve a strict same-document URI-fragment JSON Pointer."""
    if not reference.startswith("#") or _INVALID_PERCENT_ESCAPE.search(reference):
        return None
    try:
        fragment = unquote(reference[1:], encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    if not fragment:
        return root, ()
    if not fragment.startswith("/"):
        return None
    decoded: list[str] = []
    for encoded in fragment[1:].split("/"):
        index = 0
        while index < len(encoded):
            if encoded[index] == "~" and (
                index + 1 == len(encoded) or encoded[index + 1] not in "01"
            ):
                return None
            index += 2 if encoded[index] == "~" else 1
        decoded.append(encoded.replace("~1", "/").replace("~0", "~"))
    path = tuple(decoded)
    target: object = root
    for part in path:
        if isinstance(target, dict) and part in target:
            target = target[part]
        elif isinstance(target, list) and _ARRAY_INDEX.fullmatch(part) and int(part) < len(target):
            target = target[int(part)]
        else:
            return None
    return target, path


def validate_omitted_root_constraints(
    model: type[BaseModel], schema: dict[str, Any], omitted: set[str]
) -> None:
    """Reject root-object constraints that cannot survive property omission safely.

    Applicators and local references retain the current instance, so they are
    traversed. Property, item, and additional-property schemas evaluate child
    instances and deliberately are not. The validator reports unsafe constraints;
    it never rewrites them.
    """
    if not omitted:
        return

    omitted_names = ", ".join(repr(name) for name in sorted(omitted))
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        error_path = tuple(str(part) for part in error.path)
        raise WorkflowFormContractError(
            f"{model.__qualname__} cannot safely omit form properties {omitted_names}; "
            f"invalid JSON Schema at {_json_pointer(error_path)} ({error.message})"
        ) from error

    schema_id = schema.get("$id")
    document_scope = schema_id if isinstance(schema_id, str) else "#"
    root_instance: tuple[str, ...] = ()
    visited: set[tuple[int, str, tuple[str, ...]]] = set()

    def fail_unsupported(path: tuple[str, ...], reason: str) -> Never:
        raise WorkflowFormContractError(
            f"{model.__qualname__} cannot safely omit form properties {omitted_names}; "
            f"{reason} at {_json_pointer(path)}"
        )

    def check_name(name: object, path: tuple[str, ...]) -> None:
        if isinstance(name, str) and name in omitted:
            raise WorkflowFormContractError(
                f"{model.__qualname__} cannot omit form property {name!r}; its root JSON "
                f"Schema references it at {_json_pointer(path)}"
            )

    def walk(
        node: object,
        path: tuple[str, ...],
        *,
        instance_scope: tuple[str, ...] = root_instance,
        projection_root: bool = False,
    ) -> None:
        if not isinstance(node, dict):
            return
        node = cast(dict[str, Any], node)
        visit = (id(node), document_scope, instance_scope)
        if visit in visited:
            return
        visited.add(visit)

        for keyword in node:
            if keyword not in _KNOWN_ROOT_KEYWORDS:
                fail_unsupported(
                    (*path, keyword),
                    "unrecognized root-scope JSON Schema keyword",
                )

        if "$id" in node and path:
            fail_unsupported(
                (*path, "$id"),
                "embedded JSON Schema resources are unsupported",
            )
        dialect = node.get("$schema")
        if "$schema" in node and (
            path
            or not isinstance(dialect, str)
            or dialect.removesuffix("#") != _DRAFT_2020_12_SCHEMA_URI
        ):
            fail_unsupported(
                (*path, "$schema"),
                "nested or non-draft-2020-12 JSON Schema dialects are unsupported",
            )
        for keyword in _UNSUPPORTED_ROOT_KEYWORDS:
            if keyword in node:
                fail_unsupported(
                    (*path, keyword),
                    f"root-scope JSON Schema keyword {keyword!r} is unsupported",
                )

        for keyword in _WHOLE_OBJECT_ASSERTIONS:
            if keyword in node:
                assertion_path = (*path, keyword)
                raise WorkflowFormContractError(
                    f"{model.__qualname__} cannot safely omit form properties "
                    f"{omitted_names}; the root JSON Schema constraint at "
                    f"{_json_pointer(assertion_path)} evaluates the whole object"
                )

        default = node.get("default")
        if isinstance(default, dict):
            for name in default:
                check_name(name, (*path, "default", name))

        if not projection_root:
            required = node.get("required")
            if isinstance(required, list):
                for index, name in enumerate(required):
                    check_name(name, (*path, "required", str(index)))

            properties = node.get("properties")
            if isinstance(properties, dict):
                for name in properties:
                    check_name(name, (*path, "properties", name))

        dependent_required = node.get("dependentRequired")
        if isinstance(dependent_required, dict):
            for trigger, dependencies in dependent_required.items():
                check_name(trigger, (*path, "dependentRequired", trigger))
                if isinstance(dependencies, list):
                    for index, name in enumerate(dependencies):
                        check_name(
                            name,
                            (*path, "dependentRequired", trigger, str(index)),
                        )

        dependent_schemas = node.get("dependentSchemas")
        if isinstance(dependent_schemas, dict):
            for trigger, dependent_schema in dependent_schemas.items():
                dependent_path = (*path, "dependentSchemas", trigger)
                check_name(trigger, dependent_path)
                walk(dependent_schema, dependent_path)

        definitions = node.get("definitions")
        if definitions is not None and not isinstance(definitions, dict):
            fail_unsupported(
                (*path, "definitions"),
                "legacy JSON Schema definitions must be an object",
            )

        dependencies = node.get("dependencies")
        if dependencies is not None and not isinstance(dependencies, dict):
            fail_unsupported(
                (*path, "dependencies"),
                "legacy JSON Schema dependencies must be an object",
            )
        if isinstance(dependencies, dict):
            for trigger, dependency in dependencies.items():
                dependency_path = (*path, "dependencies", trigger)
                check_name(trigger, dependency_path)
                if isinstance(dependency, list):
                    if (
                        not dependency
                        or not all(isinstance(name, str) for name in dependency)
                        or len(set(dependency)) != len(dependency)
                    ):
                        fail_unsupported(
                            dependency_path,
                            "legacy property dependencies must be nonempty unique string arrays",
                        )
                    for index, name in enumerate(dependency):
                        check_name(name, (*dependency_path, str(index)))
                elif isinstance(dependency, dict | bool):
                    walk(dependency, dependency_path)
                else:
                    fail_unsupported(
                        dependency_path,
                        "legacy dependencies must be schemas or string arrays",
                    )

        for keyword in _SAME_INSTANCE_ARRAY_APPLICATORS:
            candidates = node.get(keyword)
            if isinstance(candidates, list):
                for index, candidate in enumerate(candidates):
                    walk(candidate, (*path, keyword, str(index)))

        walk(node.get("not"), (*path, "not"))
        if "if" in node and ("then" in node or "else" in node):
            walk(node["if"], (*path, "if"))
            if "then" in node:
                walk(node["then"], (*path, "then"))
            if "else" in node:
                walk(node["else"], (*path, "else"))

        reference = node.get("$ref")
        if isinstance(reference, str):
            resolved = _local_ref_target(schema, reference)
            if resolved is None:
                fail_unsupported(
                    (*path, "$ref"),
                    "only valid same-document JSON Pointer references are supported",
                )
            target, target_path = resolved
            walk(target, target_path, instance_scope=instance_scope)

    walk(schema, (), projection_root=True)
