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
"""A deterministic summary of one registry build, for comparing processes and images."""

import hashlib
import json
import sys
from dataclasses import asdict, dataclass

from nv_config_manager_workflows.registration.contract import (
    activity_name,
    workflow_class_name,
    workflow_type_name,
)
from nv_config_manager_workflows.registration.errors import WorkflowRegistrationError
from nv_config_manager_workflows.registration.registry import PluginInfo, WorkflowRegistry


@dataclass(frozen=True, slots=True)
class RegistryManifest:
    """The public identifiers of one registry build and their fingerprint.

    Holds only plugin names, versions, counts, and Temporal/scheduler names: never
    class paths, descriptor metadata, or configuration. The fingerprint detects
    plugin-set and name skew between processes; it is not a security signature
    and does not change when code changes under an unchanged version.
    """

    plugins: tuple[PluginInfo, ...]
    workflows: tuple[str, ...]
    activities: tuple[str, ...]
    schedulers: tuple[str, ...]
    fingerprint: str


def registry_manifest(registry: WorkflowRegistry) -> RegistryManifest:
    """Summarize ``registry`` with names sorted and plugins in registry order."""
    plugins = tuple(registry.plugin_diagnostics)
    workflows = tuple(
        sorted(workflow_type_name(w) or workflow_class_name(w) for w in registry.all_workflows)
    )
    activities = tuple(
        sorted(activity_name(a) or getattr(a, "__name__", "") for a in registry.all_activities)
    )
    schedulers = tuple(sorted(r.identity for r in registry.scheduler_registrations))
    content = {
        "plugins": [asdict(plugin) for plugin in plugins],
        "workflows": workflows,
        "activities": activities,
        "schedulers": schedulers,
    }
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return RegistryManifest(
        plugins=plugins,
        workflows=workflows,
        activities=activities,
        schedulers=schedulers,
        fingerprint=f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}",
    )


def main() -> None:
    """Print the installed registry manifest as JSON; exit 1 if the build fails."""
    try:
        registry = WorkflowRegistry.build()
    except WorkflowRegistrationError as error:
        print(f"Workflow registry build failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    print(json.dumps(asdict(registry_manifest(registry)), indent=2, sort_keys=True))
