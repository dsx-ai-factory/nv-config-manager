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
"""Offline validation for one installed workflow plugin's forms and providers."""

import argparse
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from nv_config_manager_workflows.registration.builtin import BUILTIN_PLUGIN_NAME
from nv_config_manager_workflows.registration.descriptor import WorkflowPluginDescriptor
from nv_config_manager_workflows.registration.discovery import discover_workflow_plugins
from nv_config_manager_workflows.registration.form_catalog import WorkflowFormCatalog
from nv_config_manager_workflows.registration.form_provider_validation import (
    resolve_form_option_provider,
)
from nv_config_manager_workflows.registration.registry import WorkflowRegistry
from nv_config_manager_workflows.ui import WorkflowFormContractError


@dataclass(frozen=True, slots=True)
class PluginFormValidationReport:
    """Summary of a successful, side-effect-free plugin form validation."""

    plugin: str
    workflow_count: int
    form_count: int
    provider_count: int
    warnings: tuple[str, ...]


def validate_plugin_forms(
    plugin: str,
    *,
    plugins: Mapping[str, WorkflowPluginDescriptor] | None = None,
) -> PluginFormValidationReport:
    """Validate one plugin's registry, forms, and referenced provider targets.

    Resolver functions are imported and inspected but never invoked. The
    built-in plugin is included when it is installed so required built-in
    activities and cross-catalog conflicts are checked as they are at startup.
    Unrelated third-party descriptors are intentionally left out.
    """
    discovered = dict(discover_workflow_plugins() if plugins is None else plugins)
    if plugin not in discovered:
        available = ", ".join(sorted(discovered)) or "none"
        raise WorkflowFormContractError(
            f"workflow plugin {plugin!r} is not installed; available plugins: {available}"
        )
    selected = {
        name: descriptor
        for name, descriptor in discovered.items()
        if name in {BUILTIN_PLUGIN_NAME, plugin}
    }
    registry = WorkflowRegistry.build(selected)
    catalog = WorkflowFormCatalog.build(registry)

    diagnostics = [
        diagnostic for diagnostic in catalog.diagnostics.values() if diagnostic.plugin == plugin
    ]
    if diagnostics:
        details = "; ".join(
            f"{diagnostic.workflow}: {diagnostic.message}" for diagnostic in diagnostics
        )
        raise WorkflowFormContractError(
            f"workflow plugin {plugin!r} has unavailable forms: {details}"
        )

    bindings = tuple(binding for binding in catalog.providers if binding.plugin == plugin)
    for binding in bindings:
        resolve_form_option_provider(binding)

    workflows = tuple(
        workflow for workflow in registry.api_workflows if registry.owner(workflow) == plugin
    )
    forms = tuple(workflow for workflow in catalog.forms if registry.owner(workflow) == plugin)
    warnings = tuple(
        f"{warning.workflow}: {warning.message}"
        for warning in catalog.warnings
        if warning.plugin == plugin
    )
    return PluginFormValidationReport(
        plugin=plugin,
        workflow_count=len(workflows),
        form_count=len(forms),
        provider_count=len(bindings),
        warnings=warnings,
    )


def main(argv: Sequence[str] | None = None) -> None:
    """Validate one installed plugin and exit nonzero on a contract error."""
    parser = argparse.ArgumentParser(
        description="Validate an installed workflow plugin's forms and option providers."
    )
    parser.add_argument("plugin", help="entry-point name in nv_config_manager.workflows")
    args = parser.parse_args(argv)
    try:
        report = validate_plugin_forms(args.plugin)
    except Exception as error:  # noqa: BLE001 - CLI boundary reports registry subclasses too
        print(f"Workflow plugin form validation failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    for warning in report.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print(
        f"Validated {report.plugin!r}: {report.workflow_count} API workflows, "
        f"{report.form_count} forms, {report.provider_count} option providers"
    )


__all__ = ["PluginFormValidationReport", "main", "validate_plugin_forms"]
