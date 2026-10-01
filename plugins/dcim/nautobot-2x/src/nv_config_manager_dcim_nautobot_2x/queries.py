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
"""Access to validated, composable GraphQL documents for the Nautobot provider."""

from __future__ import annotations

from functools import cache
from importlib.resources import files
from pathlib import PurePosixPath

from graphql import (
    DocumentNode,
    FragmentDefinitionNode,
    FragmentSpreadNode,
    OperationDefinitionNode,
    SelectionSetNode,
    parse,
    print_ast,
)


class GraphQLOperation(str):
    """A validated document paired with the operation to execute from it."""

    operation_name: str | None

    def __new__(cls, document: str, operation_name: str | None) -> GraphQLOperation:
        instance = super().__new__(cls, document)
        instance.operation_name = operation_name
        return instance


_LOADING_GRAPHQL_SOURCES: set[str] = set()


def _import_path(source_file: str, import_target: str) -> str:
    """Resolve one document-local import without allowing path traversal."""
    import_path = PurePosixPath(source_file).parent / import_target
    if import_path.is_absolute():
        raise ValueError(f"Invalid GraphQL import {import_target!r} in {source_file!r}")
    normalized_parts: list[str] = []
    for part in import_path.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not normalized_parts:
                raise ValueError(f"Invalid GraphQL import {import_target!r} in {source_file!r}")
            normalized_parts.pop()
            continue
        normalized_parts.append(part)
    return PurePosixPath(*normalized_parts).as_posix()


def _fragment_spreads(selection_set: SelectionSetNode) -> set[str]:
    """Return fragment names directly or transitively referenced by a selection set."""
    names: set[str] = set()
    for selection in selection_set.selections:
        if isinstance(selection, FragmentSpreadNode):
            names.add(selection.name.value)
        nested_selection_set = getattr(selection, "selection_set", None)
        if nested_selection_set is not None:
            names.update(_fragment_spreads(nested_selection_set))
    return names


@cache
def _load_graphql_source(filename: str) -> str:
    """Load one document and recursively prepend its explicitly imported fragments."""
    if filename in _LOADING_GRAPHQL_SOURCES:
        raise ValueError(f"Circular GraphQL import detected: {filename!r}")
    _LOADING_GRAPHQL_SOURCES.add(filename)
    try:
        source = files(__package__).joinpath("graphql", filename).read_text(encoding="utf-8")
        imported_sources: list[str] = []
        retained_lines: list[str] = []
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("# import "):
                target = stripped.removeprefix("# import ").strip().strip('"')
                imported_sources.append(_load_graphql_source(_import_path(filename, target)))
                continue
            retained_lines.append(line)
        return "\n\n".join((*imported_sources, "\n".join(retained_lines)))
    finally:
        _LOADING_GRAPHQL_SOURCES.discard(filename)


@cache
def load_graphql_query(filename: str, operation_name: str | None = None) -> GraphQLOperation:
    """Load and syntax-check a provider document, selecting a named operation when needed.

    Documents may import reusable fragments with ``# import "relative/path.graphql"``.
    A document containing more than one operation must provide ``operation_name`` so the
    Nautobot transport can send it as GraphQL's standard ``operationName`` field.
    """
    source = _load_graphql_source(filename)
    document = parse(source)
    operations = [
        definition
        for definition in document.definitions
        if isinstance(definition, OperationDefinitionNode)
    ]
    names = {operation.name.value for operation in operations if operation.name is not None}
    if operation_name is not None and operation_name not in names:
        raise ValueError(f"GraphQL operation {operation_name!r} not found in {filename!r}")
    if operation_name is None and len(operations) != 1:
        raise ValueError(f"GraphQL document {filename!r} requires an operation name")
    selected_operation = next(
        (
            operation
            for operation in operations
            if operation_name is None
            or (operation.name is not None and operation.name.value == operation_name)
        ),
        None,
    )
    if selected_operation is None:
        raise ValueError(f"GraphQL document {filename!r} does not define an executable operation")

    fragments = {
        definition.name.value: definition
        for definition in document.definitions
        if isinstance(definition, FragmentDefinitionNode)
    }
    required_fragments: list[FragmentDefinitionNode] = []
    pending_fragment_names = _fragment_spreads(selected_operation.selection_set)
    while pending_fragment_names:
        fragment_name = pending_fragment_names.pop()
        fragment = fragments.get(fragment_name)
        if fragment is None:
            raise ValueError(f"GraphQL fragment {fragment_name!r} is not defined in {filename!r}")
        if fragment in required_fragments:
            continue
        required_fragments.append(fragment)
        pending_fragment_names.update(_fragment_spreads(fragment.selection_set))

    selected_document = DocumentNode(
        definitions=(selected_operation, *required_fragments),
    )
    return GraphQLOperation(print_ast(selected_document), operation_name)


@cache
def load_graphql_selection(filename: str) -> str:
    """Load and validate a reusable GraphQL selection-set fragment.

    Selection fragments are intentionally distinct from named GraphQL fragments: they
    are used to preserve the legacy ``get_device(s, fields=...)`` extension point,
    where callers supply arbitrary field selections.
    """
    source = _load_graphql_source(filename)
    parse(f"query ValidateSelection {{ {source} }}")
    return source


def render_graphql_fields_template(filename: str, fields: str) -> str:
    """Insert a legacy field selection into a provider-owned query template.

    ``fields`` is a public compatibility extension point, so validate the fully
    rendered document rather than limiting callers to the built-in selection files.
    """
    rendered = str(load_graphql_query(filename)).replace("__FIELDS__", fields)
    parse(rendered)
    return rendered
