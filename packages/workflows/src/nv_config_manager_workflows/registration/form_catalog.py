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
"""Browser-form catalog built separately from the workflow execution registry."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Self, cast

from nv_config_manager_workflows.form_declarations import FormOptionProvider
from nv_config_manager_workflows.metadata import WorkflowMetadataMixin
from nv_config_manager_workflows.registration.builtin import BUILTIN_PLUGIN_NAME
from nv_config_manager_workflows.registration.contract import workflow_form_enabled
from nv_config_manager_workflows.registration.registry import WorkflowRegistry
from nv_config_manager_workflows.ui import (
    FormOptionSource,
    OptionSource,
    WorkflowFormContractError,
    build_form,
)
from nv_config_manager_workflows.ui.wire_validation import validate_form_envelope

_MAX_DIAGNOSTIC_LENGTH = 1000
WORKFLOW_FORM_ID_PATTERN = r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$"
_WORKFLOW_FORM_ID = re.compile(WORKFLOW_FORM_ID_PATTERN)
_PROVIDER_SLUG = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_IMPORT_REFERENCE = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$")


@dataclass(frozen=True, slots=True)
class WorkflowFormDiagnostic:
    """Why a third-party workflow's form is unavailable; its workflow stays registered."""

    plugin: str
    workflow: str
    message: str


@dataclass(frozen=True, slots=True)
class WorkflowFormWarning:
    """A non-fatal issue with an unused form-provider declaration."""

    plugin: str
    workflow: str
    message: str


@dataclass(frozen=True, slots=True)
class FormOptionProviderBinding:
    """One referenced provider ready for API-only resolver compilation."""

    workflow: type[WorkflowMetadataMixin]
    workflow_form_id: str
    plugin: str
    source: str
    endpoint: str
    declaration: FormOptionProvider
    uses: tuple[FormOptionSource, ...]


@dataclass
class WorkflowFormCatalog:
    """Validated browser forms for one workflow-registry snapshot."""

    forms: dict[type[WorkflowMetadataMixin], dict[str, Any]] = field(default_factory=dict)
    form_ids: dict[type[WorkflowMetadataMixin], str] = field(default_factory=dict)
    diagnostics: dict[type[WorkflowMetadataMixin], WorkflowFormDiagnostic] = field(
        default_factory=dict
    )
    providers: tuple[FormOptionProviderBinding, ...] = ()
    warnings: tuple[WorkflowFormWarning, ...] = ()

    @classmethod
    def build(cls, registry: WorkflowRegistry) -> Self:
        """Build enabled API forms without changing the execution registry.

        Invalid third-party forms become diagnostics and leave execution
        registration intact. Invalid built-in forms remain fatal for the API
        process and CI form validation.
        """
        forms: dict[type[WorkflowMetadataMixin], dict[str, Any]] = {}
        form_ids, diagnostics = _resolve_workflow_form_ids(registry)
        providers: list[FormOptionProviderBinding] = []
        warnings: list[WorkflowFormWarning] = []
        for workflow, resolved_form_id in form_ids.items():
            owner = registry.owner(workflow)
            try:
                declarations = workflow.get_workflow_form_option_providers()
                uses: dict[str, list[FormOptionSource]] = {}

                def compile_option_source(
                    source: FormOptionSource,
                    *,
                    current_workflow: type[WorkflowMetadataMixin] = workflow,
                    current_workflow_form_id: str = resolved_form_id,
                    current_declarations: object = declarations,
                    current_uses: dict[str, list[FormOptionSource]] = uses,
                ) -> OptionSource:
                    _resolve_provider_declaration(current_workflow, current_declarations, source)
                    current_uses.setdefault(source.name, []).append(source)
                    return OptionSource(
                        _provider_endpoint(current_workflow_form_id, source.name),
                        "label",
                        "value",
                        params=source.params,
                        depends_on=source.depends_on,
                        clear_on_change=source.clear_on_change,
                        response="options-v1",
                    )

                candidate = build_form(
                    workflow.get_workflow_input_class(),
                    compile_option_source=compile_option_source,
                )
                validate_form_envelope(candidate)
                workflow_providers: list[FormOptionProviderBinding] = []
                for source, source_uses in uses.items():
                    declaration = declarations[source]
                    workflow_providers.append(
                        FormOptionProviderBinding(
                            workflow=workflow,
                            workflow_form_id=resolved_form_id,
                            plugin=owner,
                            source=source,
                            endpoint=_provider_endpoint(resolved_form_id, source),
                            declaration=declaration,
                            uses=tuple(source_uses),
                        )
                    )
                workflow_warnings = _unused_provider_warnings(workflow, owner, declarations, uses)
            except Exception as error:  # noqa: BLE001 - isolate untrusted plugin hooks
                if owner == BUILTIN_PLUGIN_NAME:
                    raise
                diagnostics[workflow] = _diagnostic(
                    owner,
                    workflow,
                    _exception_message(error),
                )
                continue
            forms[workflow] = candidate
            providers.extend(workflow_providers)
            warnings.extend(workflow_warnings)
        return cls(
            forms=forms,
            form_ids=form_ids,
            diagnostics=diagnostics,
            providers=tuple(providers),
            warnings=tuple(warnings),
        )

    def unavailable(
        self,
        workflow: type[WorkflowMetadataMixin],
        error: Exception | str,
    ) -> None:
        """Atomically withdraw a provider-backed form after API compilation fails.

        Built-in failures stay fatal. Third-party failures remove both the form
        and every provider binding for it, while preserving workflow execution.
        """
        bindings = tuple(binding for binding in self.providers if binding.workflow is workflow)
        if not bindings:
            raise KeyError(f"{workflow.__name__} has no form-option provider binding")
        plugin = bindings[0].plugin
        message = str(error)
        if plugin == BUILTIN_PLUGIN_NAME:
            if isinstance(error, WorkflowFormContractError):
                raise error
            if isinstance(error, Exception):
                raise WorkflowFormContractError(message) from error
            raise WorkflowFormContractError(message)
        self.forms.pop(workflow, None)
        self.providers = tuple(
            binding for binding in self.providers if binding.workflow is not workflow
        )
        self.diagnostics[workflow] = WorkflowFormDiagnostic(
            plugin=plugin,
            workflow=workflow.__name__,
            message=_sanitize(message),
        )


def _resolve_provider_declaration(
    workflow: type[WorkflowMetadataMixin],
    declarations: object,
    source: FormOptionSource,
) -> FormOptionProvider:
    """Return and validate the inert provider declaration named by ``source``."""
    where = f"{workflow.__name__} form option provider {source.name!r}"
    if not _PROVIDER_SLUG.fullmatch(source.name):
        raise WorkflowFormContractError(f"{where} must match {_PROVIDER_SLUG.pattern!r}")
    if not isinstance(declarations, Mapping):
        raise WorkflowFormContractError(
            f"{workflow.__name__}.workflow_form_option_providers must be a mapping"
        )
    declaration = cast(Mapping[object, object], declarations).get(source.name)
    if declaration is None:
        raise WorkflowFormContractError(f"{where} is referenced by the form but is not declared")
    if not isinstance(declaration, FormOptionProvider):
        raise WorkflowFormContractError(
            f"{where} must be a FormOptionProvider; got {type(declaration).__name__}"
        )
    for attribute in ("resolver", "query_model"):
        reference = getattr(declaration, attribute)
        if not isinstance(reference, str) or not _IMPORT_REFERENCE.fullmatch(reference):
            raise WorkflowFormContractError(
                f"{where} {attribute} must be a 'module.path:attribute' reference; "
                f"got {reference!r}"
            )
    if not isinstance(source.params, Mapping):
        raise WorkflowFormContractError(f"{where} params must be a mapping")
    if not isinstance(source.depends_on, Mapping):
        raise WorkflowFormContractError(f"{where} depends_on must be a mapping")
    overlap = set(source.params) & set(source.depends_on)
    if overlap:
        raise WorkflowFormContractError(
            f"{where} supplies query parameters in both params and depends_on: {sorted(overlap)}"
        )
    return declaration


def _provider_endpoint(workflow_form_id: str, source: str) -> str:
    return f"/v1/workflow/{workflow_form_id}/form-options/{source}"


def _resolve_workflow_form_ids(
    registry: WorkflowRegistry,
) -> tuple[
    dict[type[WorkflowMetadataMixin], str],
    dict[type[WorkflowMetadataMixin], WorkflowFormDiagnostic],
]:
    """Validate stable form IDs and isolate third-party contract failures."""
    candidates: dict[str, list[type[WorkflowMetadataMixin]]] = {}
    diagnostics: dict[type[WorkflowMetadataMixin], WorkflowFormDiagnostic] = {}
    for workflow in registry.api_workflows:
        owner = registry.owner(workflow)
        try:
            if not workflow_form_enabled(workflow):
                continue
            form_id = workflow.get_workflow_form_id()
            form_id_error = _workflow_form_id_error(workflow, form_id)
            if form_id_error is not None:
                raise WorkflowFormContractError(form_id_error)
            assert isinstance(form_id, str)
            candidates.setdefault(form_id, []).append(workflow)
        except Exception as error:  # noqa: BLE001 - isolate untrusted plugin accessors
            if owner == BUILTIN_PLUGIN_NAME:
                raise
            diagnostics[workflow] = _diagnostic(
                owner,
                workflow,
                _exception_message(error),
            )
            continue

    form_ids: dict[type[WorkflowMetadataMixin], str] = {}
    for form_id, workflows in candidates.items():
        if len(workflows) == 1:
            form_ids[workflows[0]] = form_id
            continue
        builtins = [
            workflow for workflow in workflows if registry.owner(workflow) == BUILTIN_PLUGIN_NAME
        ]
        if len(builtins) > 1:
            names = ", ".join(workflow.__name__ for workflow in builtins)
            raise WorkflowFormContractError(
                f"workflow_form_id {form_id!r} is declared by multiple built-in workflows: {names}"
            )
        if builtins:
            winner = builtins[0]
            form_ids[winner] = form_id
            conflicts = [workflow for workflow in workflows if workflow is not winner]
        else:
            conflicts = workflows
        for workflow in conflicts:
            owner = registry.owner(workflow)
            diagnostics[workflow] = _diagnostic(
                owner,
                workflow,
                f"workflow_form_id {form_id!r} conflicts with another workflow",
            )
    return form_ids, diagnostics


def _workflow_form_id_error(workflow: type[WorkflowMetadataMixin], form_id: object) -> str | None:
    """Return a contract error for one workflow form ID, if any."""
    where = f"{workflow.__name__}.workflow_form_id"
    if not isinstance(form_id, str) or not form_id:
        return f"{where} must be an explicit non-empty string"
    if not _WORKFLOW_FORM_ID.fullmatch(form_id):
        return f"{where} must match {_WORKFLOW_FORM_ID.pattern!r}; got {form_id!r}"
    return None


def _diagnostic(
    plugin: str,
    workflow: type[WorkflowMetadataMixin],
    message: str,
) -> WorkflowFormDiagnostic:
    """Build a sanitized third-party form diagnostic."""
    return WorkflowFormDiagnostic(
        plugin=plugin,
        workflow=workflow.__name__,
        message=_sanitize(message),
    )


def _exception_message(error: Exception) -> str:
    """Describe an unexpected plugin exception without exposing a traceback."""
    try:
        message = str(error)
    except Exception:  # noqa: BLE001 - even plugin exception formatting is untrusted
        message = "could not be rendered"
    if isinstance(error, WorkflowFormContractError):
        return message
    return f"{type(error).__name__}: {message}"


def _unused_provider_warnings(
    workflow: type[WorkflowMetadataMixin],
    plugin: str,
    declarations: object,
    uses: Mapping[str, list[FormOptionSource]],
) -> list[WorkflowFormWarning]:
    """Report declarations that were not referenced, without validating/importing them."""
    if not isinstance(declarations, Mapping):
        if uses:
            return []  # A referenced source already turned this into a form error.
        return [
            WorkflowFormWarning(
                plugin=plugin,
                workflow=workflow.__name__,
                message="workflow_form_option_providers is unused and is not a mapping",
            )
        ]
    return [
        WorkflowFormWarning(
            plugin=plugin,
            workflow=workflow.__name__,
            message=f"form option provider {source!r} is declared but not referenced",
        )
        for source in declarations
        if source not in uses
    ]


def _sanitize(message: str) -> str:
    """Return a single-line, printable, bounded diagnostic message."""
    printable = "".join(c if c.isprintable() else " " for c in message)
    collapsed = " ".join(printable.split())
    if len(collapsed) > _MAX_DIAGNOSTIC_LENGTH:
        return collapsed[: _MAX_DIAGNOSTIC_LENGTH - 3] + "..."
    return collapsed
