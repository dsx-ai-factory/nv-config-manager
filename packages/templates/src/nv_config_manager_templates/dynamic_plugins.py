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
"""Load template plugins that were activated dynamically.

A dynamically activated plugin is not an installed Python package: it is a directory extracted
into ``$NVCM_DYNAMIC_PLUGINS_DIR/<plugin id>/`` containing a ``plugin.yaml`` manifest, template
files, and optionally a Python module and GraphQL query files. The static mechanism (entry points
of installed packages, see ``Renderer._discover_plugins``) is unchanged; this module produces
plugin records in the same shape so the renderer treats both kinds identically.

The loader is inert unless ``NVCM_DYNAMIC_PLUGINS_DIR`` is set, which the Helm chart does only
when the dynamic plugin gate is fully open, so with the gate closed the render path is exactly as
before.

A plugin that fails to load is skipped and reported in ``DynamicPluginLoadResult.errors``; it
never prevents other plugins, or the renderer itself, from loading. Plugin Python runs with the
renderer's own privileges: this is the arbitrary-code-execution capability the gate and the
approval workflow exist to control, not something this module sandboxes.
"""

from __future__ import annotations

import importlib
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import Version

logger = logging.getLogger(__name__)

DYNAMIC_PLUGINS_DIR_ENV = "NVCM_DYNAMIC_PLUGINS_DIR"
MANIFEST_FILENAME = "plugin.yaml"
MANIFEST_API_GROUP = "nvconfigmanager.nvidia.com/"
MANIFEST_KIND = "TemplatePlugin"

# The plugin-facing contract of this renderer (the hooks and data shapes below). A manifest
# declares the range it was written for in compatibility.templateEngineAPI and is skipped when
# this renderer's version is outside it.
TEMPLATE_ENGINE_API_VERSION = "1"


class DynamicPluginError(Exception):
    """A dynamically activated plugin could not be loaded."""


@dataclass
class DynamicPluginLoadResult:
    """Plugins that loaded, and one message per plugin directory that did not."""

    plugins: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def dynamic_plugins_dir() -> Path | None:
    """The configured directory, or None when dynamic plugins are not enabled."""
    value = os.environ.get(DYNAMIC_PLUGINS_DIR_ENV, "").strip()
    return Path(value) if value else None


def load_dynamic_plugins(root: Path | None = None) -> DynamicPluginLoadResult:
    """Load every plugin directory under ``root`` (default: the configured directory).

    Returns an empty result, without touching the filesystem further, when no directory is
    configured or it does not exist (nothing has been activated yet).
    """
    result = DynamicPluginLoadResult()
    root = root if root is not None else dynamic_plugins_dir()
    if root is None or not root.is_dir():
        return result

    for plugin_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        manifest_path = plugin_dir / MANIFEST_FILENAME
        if not manifest_path.is_file():
            continue  # not a dynamic plugin directory
        try:
            result.plugins.append(_load_plugin(plugin_dir, manifest_path))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            message = f"{plugin_dir.name}: {exc}"
            logger.warning("Skipping dynamic plugin %s", message)
            result.errors.append(message)
    return result


def _load_plugin(plugin_dir: Path, manifest_path: Path) -> dict[str, Any]:
    manifest = _read_manifest(manifest_path)
    plugin_id = manifest["metadata"]["id"]
    _check_compatibility(manifest)

    root = plugin_dir.resolve()
    template_paths = [
        _inside(root, relative, "templates.paths")
        for relative in _string_list(
            manifest.get("templates", {}).get("paths", []), "templates.paths"
        )
    ]
    data_requirements = {
        name: {"graphql_query": _inside(root, relative, f"graphql.queries.{name}").read_text()}
        for name, relative in _string_map(
            manifest.get("graphql", {}).get("queries", {}), "graphql.queries"
        ).items()
    }

    plugin: dict[str, Any] = {
        "name": f"dynamic:{plugin_id}",
        "module": None,
        "template_paths": template_paths,
        "filters": {},
        "data_requirements": data_requirements,
        "dynamic": True,
        "plugin_id": plugin_id,
        "display_version": str(manifest["metadata"].get("displayVersion", "")),
    }

    entrypoint = manifest["python"]["entrypoint"]
    module = _import_entrypoint(root, entrypoint)
    plugin["module"] = module
    if hasattr(module, "get_template_paths"):
        extra = module.get_template_paths()
        for path in extra if isinstance(extra, list) else [extra]:
            plugin["template_paths"].append(_inside(root, str(path), "get_template_paths()"))
    if hasattr(module, "get_custom_filters"):
        plugin["filters"] = dict(module.get_custom_filters())
    if hasattr(module, "get_render_data_requirements"):
        plugin["data_requirements"].update(module.get_render_data_requirements())

    missing = [str(p) for p in plugin["template_paths"] if not p.is_dir()]
    if missing:
        raise DynamicPluginError(f"template path(s) not found: {', '.join(missing)}")

    logger.info(
        "Loaded dynamic plugin %s %s: %d template path(s), %d filter(s), %d data requirement(s)",
        plugin_id,
        plugin["display_version"],
        len(plugin["template_paths"]),
        len(plugin["filters"]),
        len(plugin["data_requirements"]),
    )
    return plugin


def _read_manifest(manifest_path: Path) -> dict[str, Any]:
    try:
        manifest = yaml.safe_load(manifest_path.read_text())
    except yaml.YAMLError as exc:
        raise DynamicPluginError(f"{MANIFEST_FILENAME} is not valid YAML: {exc}") from exc
    if not isinstance(manifest, dict):
        raise DynamicPluginError(f"{MANIFEST_FILENAME} must be a mapping")
    if not str(manifest.get("apiVersion", "")).startswith(MANIFEST_API_GROUP):
        raise DynamicPluginError(f"unsupported apiVersion {manifest.get('apiVersion')!r}")
    if manifest.get("kind") != MANIFEST_KIND:
        raise DynamicPluginError(
            f"unsupported kind {manifest.get('kind')!r}, expected {MANIFEST_KIND!r}"
        )
    for dotted in ("metadata.id", "compatibility.templateEngineAPI", "python.entrypoint"):
        node: Any = manifest
        for key in dotted.split("."):
            node = node.get(key) if isinstance(node, dict) else None
        if not isinstance(node, str) or not node:
            raise DynamicPluginError(f"missing required field {dotted}")
    return manifest


def _check_compatibility(manifest: dict[str, Any]) -> None:
    requirement = manifest["compatibility"]["templateEngineAPI"]
    try:
        specifier = SpecifierSet(requirement)
    except InvalidSpecifier as exc:
        raise DynamicPluginError(
            f"invalid compatibility.templateEngineAPI {requirement!r}"
        ) from exc
    if Version(TEMPLATE_ENGINE_API_VERSION) not in specifier:
        raise DynamicPluginError(
            f"requires template engine API {requirement!r}, "
            f"this renderer provides {TEMPLATE_ENGINE_API_VERSION}"
        )


def _inside(root: Path, relative: str, what: str) -> Path:
    """Resolve ``relative`` under ``root``, refusing anything that leaves it.

    Absolute paths and ``..`` components that escape the plugin directory are refused.
    """
    resolved = (root / relative).resolve()
    if resolved != root and root not in resolved.parents:
        raise DynamicPluginError(f"{what} {relative!r} points outside the plugin directory")
    return resolved


def _string_list(value: Any, what: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise DynamicPluginError(f"{what} must be a list of strings")
    return value


def _string_map(value: Any, what: str) -> dict[str, str]:
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        raise DynamicPluginError(f"{what} must map names to file paths")
    return value


def _import_entrypoint(root: Path, entrypoint: str) -> Any:
    # Appended, not prepended: a plugin must not be able to shadow the standard library or the
    # renderer's own modules just by shipping a module of the same name.
    if str(root) not in sys.path:
        sys.path.append(str(root))
    try:
        module = importlib.import_module(entrypoint)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise DynamicPluginError(
            f"python.entrypoint {entrypoint!r} failed to import: {exc}"
        ) from exc
    _require_module_from(root, module, entrypoint)
    return module


def _module_location(module: Any) -> Path | None:
    """Where an imported module (or package) lives on disk, or None when it has no location."""
    location = getattr(module, "__file__", None)
    if location is None:
        search_path = list(getattr(module, "__path__", []) or [])
        location = search_path[0] if search_path else None
    return Path(location).resolve() if location else None


def _require_module_from(root: Path, module: Any, entrypoint: str) -> None:
    """Refuse an entrypoint that resolved to a module from somewhere other than this plugin.

    Python looks in ``sys.modules`` before it searches ``sys.path``, so when two plugins (or a
    plugin and an installed package) use the same entrypoint name, the second import silently
    returns the first module and the second plugin would be registered with the first one's
    filters and hooks.
    """
    location = _module_location(module)
    plugin_root = root.resolve()
    if location is None or (location != plugin_root and plugin_root not in location.parents):
        where = f" ({location})" if location else ""
        raise DynamicPluginError(
            f"python.entrypoint {entrypoint!r} resolves to a module{where} that is not in this "
            "plugin: another plugin or an installed package already provides that name. "
            "Entrypoint names must be unique among the activated plugins."
        )
