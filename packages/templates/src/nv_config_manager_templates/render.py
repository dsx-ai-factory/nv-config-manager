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
"""Template rendering implementation."""

from __future__ import annotations

import logging
import re
import sys
from inspect import getmembers, isfunction

from jinja2 import (
    ChoiceLoader,
    Environment,
    FileSystemLoader,
    PackageLoader,
    select_autoescape,
)

try:
    from importlib.metadata import entry_points
except ImportError:
    from importlib_metadata import entry_points  # type: ignore

from nv_config_manager_dcim import DeviceRenderData, RenderData, RenderDataRequirement

import nv_config_manager_templates.filters.bgp as bgp_filters
import nv_config_manager_templates.filters.device as device_filters
import nv_config_manager_templates.filters.ip as ip_filters
import nv_config_manager_templates.filters.location as location_filters
import nv_config_manager_templates.filters.vault as vault_filters
from nv_config_manager_templates.filters import FilterException

# Modify this constant as new filter modules are implemented
FILTER_MODULES = [
    bgp_filters,
    device_filters,
    ip_filters,
    location_filters,
    vault_filters,
]

logger = logging.getLogger(__name__)


class TemplateLookupException(Exception):
    """Template Lookup Exception."""


class PluginException(Exception):
    """Plugin Exception."""


class Renderer:
    """Render templates from data supplied by the selected DCIM provider."""

    def __init__(self, enable_plugins: bool = True):
        """Initialize Renderer object.

        Args:
            enable_plugins: Whether to discover and load plugins (default: True)
        """
        # Discover plugins first
        self.plugins = []
        if enable_plugins:
            self.plugins = self._discover_plugins()

        # Set up Jinja2 environment with plugin template paths
        loaders = []

        # Add plugin template paths first (higher priority)
        for plugin in self.plugins:
            for path in plugin.get("template_paths", []):
                loaders.append(FileSystemLoader(str(path)))
                logger.info("Loaded template path from plugin: %s", path)

        # Add base template loader last (fallback)
        loaders.append(PackageLoader("nv_config_manager_templates"))

        # Create environment with ChoiceLoader
        loader = ChoiceLoader(loaders) if loaders else PackageLoader("nv_config_manager_templates")
        self.environment = Environment(
            loader=loader,
            autoescape=select_autoescape(),
            trim_blocks=True,
        )

        # Load filters from base library and plugins
        self._dynamically_load_filters()
        self._load_plugin_filters()

        # Plugins may declare provider-neutral data requirements. The active
        # DCIM provider owns fetching and normalizing all of that data before
        # invoking this engine.
        self.plugin_data_requirements = self._collect_plugin_data_requirements()

    def _discover_plugins(self) -> list[dict]:
        """Discover installed template plugins via entry points.

        Returns:
            List of plugin dictionaries with metadata and callable functions
        """
        discovered_plugins = []

        try:
            # Look for plugins registered under 'nv_config_manager_templates.plugins'
            eps = entry_points()

            # Handle different versions of importlib.metadata API
            if hasattr(eps, "select"):
                # Python 3.10+
                plugin_eps = eps.select(group="nv_config_manager_templates.plugins")
            else:
                # Python 3.9
                plugin_eps = eps.get("nv_config_manager_templates.plugins", [])

            for entry_point in plugin_eps:
                try:
                    # Load from entry point (could be function or module)
                    loaded = entry_point.load()

                    # Get the module - entry point might load function directly
                    if callable(loaded) and hasattr(loaded, "__module__"):
                        # Entry point loaded a function, get its module
                        plugin_module = sys.modules[loaded.__module__]
                    else:
                        # Entry point loaded the module directly
                        plugin_module = loaded

                    plugin_info = {
                        "name": entry_point.name,
                        "module": plugin_module,
                        "template_paths": [],
                        "filters": {},
                        "data_requirements": {},
                    }

                    # Get template paths
                    if hasattr(plugin_module, "get_template_paths"):
                        paths = plugin_module.get_template_paths()
                        plugin_info["template_paths"] = (
                            paths if isinstance(paths, list) else [paths]
                        )

                    # Get custom filters
                    if hasattr(plugin_module, "get_custom_filters"):
                        plugin_info["filters"] = plugin_module.get_custom_filters()

                    if hasattr(plugin_module, "get_render_data_requirements"):
                        plugin_info["data_requirements"] = (
                            plugin_module.get_render_data_requirements()
                        )

                    discovered_plugins.append(plugin_info)
                    logger.info(
                        "Loaded plugin: %s with %s template path(s)",
                        entry_point.name,
                        len(plugin_info["template_paths"]),
                    )

                except Exception as exc:  # pylint: disable=broad-exception-caught
                    logger.warning("Failed to load plugin %s: %s", entry_point.name, exc)

        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Error discovering plugins: %s", exc)

        return discovered_plugins

    def _dynamically_load_filters(self):
        """Dynamically load all public functions from relevant filter modules."""
        for filter_module in FILTER_MODULES:
            for name, func in getmembers(filter_module, isfunction):
                if name.startswith("_"):
                    continue
                if name in self.environment.filters:
                    raise FilterException(f"Conflicting filter names across modules: {name}")
                self.environment.filters[name] = func

    def _load_plugin_filters(self):
        """Load custom filters from discovered plugins."""
        for plugin in self.plugins:
            for name, func in plugin.get("filters", {}).items():
                if name in self.environment.filters:
                    logger.info(
                        "Plugin %s overrides filter '%s'",
                        plugin["name"],
                        name,
                    )
                self.environment.filters[name] = func
                logger.info("Loaded filter '%s' from plugin %s", name, plugin["name"])

    def _collect_plugin_data_requirements(self) -> dict[str, RenderDataRequirement]:
        """Collect provider-neutral render-data requirements from plugins.

        Returns:
            Dictionary mapping requirement names to provider-owned requirement data
        """
        requirements: dict[str, RenderDataRequirement] = {}
        for plugin in self.plugins:
            for name, requirement in plugin.get("data_requirements", {}).items():
                if name in requirements:
                    logger.warning(
                        "Plugin %s data requirement '%s' conflicts with an existing requirement; "
                        "skipping",
                        plugin["name"],
                        name,
                    )
                    continue
                if not isinstance(name, str):
                    logger.warning(
                        "Plugin %s declared a non-string render-data requirement name; skipping",
                        plugin["name"],
                    )
                    continue
                if isinstance(requirement, RenderDataRequirement):
                    requirements[name] = requirement
                elif isinstance(requirement, dict):
                    requirements[name] = RenderDataRequirement(parameters=requirement)
                else:
                    logger.warning(
                        "Plugin %s data requirement '%s' must be a mapping; skipping",
                        plugin["name"],
                        name,
                    )
                    continue
                logger.info("Registered data requirement '%s' from plugin %s", name, plugin["name"])
        return requirements

    @staticmethod
    def _normalize_string(src_string: str):
        return re.sub(r"\s+", "-", src_string).lower()

    def load_data(self, render_data: RenderData | None = None) -> RenderData:
        """Validate the complete render payload supplied by a DCIM provider."""
        if render_data is None:
            raise TemplateLookupException("Renderer requires RenderData from a DCIM provider.")
        if not isinstance(render_data, RenderData):
            raise TemplateLookupException("Renderer requires a RenderData instance.")
        return render_data

    def render_entrypoints(
        self,
        render_data: RenderData,
    ) -> dict[str, str]:
        """Render all entrypoint files for the given device."""
        render_data = self.load_data(render_data)

        # Load all Entrypoint templates
        entrypoints = self.list_entrypoints(render_data.device)

        # Render all Entrypoint templates
        files = {}
        for entrypoint in entrypoints:
            files[entrypoint] = self.render(entrypoint, render_data)
        return files

    def list_entrypoints(self, device_data: DeviceRenderData) -> list[str]:
        """List all entrypoint templates for the given device."""
        platform = self._normalize_string(device_filters.platform(device_data))
        role = self._normalize_string(device_filters.role(device_data))
        fwver = device_filters.desired_firmware(device_data)

        path = f"{platform}/{role}/{fwver}/entrypoint/"
        return [
            template for template in self.environment.list_templates() if template.startswith(path)
        ]

    def render(
        self,
        template: str,
        render_data: RenderData,
    ):
        """Execute a render from provider-owned ``RenderData``.

        Args:
            template: Template path to render
            render_data: Complete device, location, and extension data from a DCIM provider
        """
        render_data = self.load_data(render_data)
        template_obj = self.environment.get_template(template)

        # Build render context
        context = {
            "device_data": render_data.device,
            "location_data": render_data.location,
        }

        # Templates receive each extension's declared data.  The schema and
        # version remain part of the provider/cache envelope, not a new
        # provider-specific template object.
        if render_data.plugin_data:
            context["plugin_data"] = {
                name: extension.data for name, extension in render_data.plugin_data.items()
            }

        output = template_obj.render(**context)
        return re.sub(r"\n+", "\n", output).strip()
