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
"""Template engine and plugin version helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from importlib.metadata import PackageNotFoundError, entry_points, version
from typing import Any

from packaging.version import InvalidVersion, Version

PLUGIN_ENTRYPOINT_GROUP = "nv_config_manager_templates.plugins"
ENGINE_DISTRIBUTION = "nv-config-manager-templates"
LOCAL_VERSION = "0.0.0+local"
SHA_LOCAL_VERSION_RE = re.compile(r"\+(?:g)?[0-9a-f]{7,40}(?:[.-]|$)", re.IGNORECASE)


class TemplateVersionRelation(Enum):
    """Ordering relationship between two template versions."""

    OLDER = "older"
    EQUAL = "equal"
    NEWER = "newer"
    INCOMPARABLE = "incomparable"


@dataclass(frozen=True)
class TemplatePackageVersion:
    """Version for a single template package component."""

    name: str
    version: str


@dataclass(frozen=True)
class TemplateVersion:
    """Readable version vector for the template engine and installed plugins."""

    engine_version: str
    engine_name: str = ENGINE_DISTRIBUTION
    plugins: tuple[TemplatePackageVersion, ...] = ()

    def __post_init__(self) -> None:
        plugins = {plugin.name: plugin for plugin in self.plugins if plugin.name and plugin.version}
        object.__setattr__(
            self,
            "plugins",
            tuple(plugins[name] for name in sorted(plugins)),
        )

    @classmethod
    def current(cls) -> TemplateVersion:
        """Return the version vector for the current runtime."""
        return cls(
            engine_version=_distribution_version(ENGINE_DISTRIBUTION),
            plugins=tuple(
                TemplatePackageVersion(name, package_version)
                for name, package_version in installed_template_plugins()
            ),
        )

    @classmethod
    def parse(cls, value: str | TemplateVersion | None) -> TemplateVersion:
        """Parse a legacy semver or compound template version string."""
        if isinstance(value, TemplateVersion):
            return value
        if not value:
            return cls(engine_version=LOCAL_VERSION)
        if not value.startswith("engine="):
            return cls(engine_version=value)

        engine_name = ENGINE_DISTRIBUTION
        engine_version = LOCAL_VERSION
        plugins: list[TemplatePackageVersion] = []

        for section in value.split(";"):
            section_name, separator, section_value = section.partition("=")
            if not separator:
                continue
            if section_name == "engine":
                package_name, package_version = _split_package_version(section_value)
                if package_name and package_version:
                    engine_name = package_name
                    engine_version = package_version
            elif section_name == "plugins" and section_value != "none":
                for plugin in section_value.split(","):
                    package_name, package_version = _split_package_version(plugin)
                    if package_name and package_version:
                        plugins.append(TemplatePackageVersion(package_name, package_version))

        return cls(
            engine_name=engine_name,
            engine_version=engine_version,
            plugins=tuple(plugins),
        )

    def __str__(self) -> str:
        """Return the compound template version key."""
        plugin_key = ",".join(f"{plugin.name}:{plugin.version}" for plugin in self.plugins)
        return f"engine={self.engine_name}:{self.engine_version};plugins={plugin_key or 'none'}"

    def __eq__(self, other: object) -> bool:
        """Return whether two template version vectors are identical."""
        if not isinstance(other, (TemplateVersion, str)):
            return NotImplemented
        return self.compare(other) is TemplateVersionRelation.EQUAL

    def __lt__(self, other: TemplateVersion | str) -> bool:
        """Return whether this version is strictly older than another version."""
        return self.compare(other) is TemplateVersionRelation.OLDER

    def __le__(self, other: TemplateVersion | str) -> bool:
        """Return whether this version is older than or equal to another version."""
        return self.compare(other) in {
            TemplateVersionRelation.OLDER,
            TemplateVersionRelation.EQUAL,
        }

    def __gt__(self, other: TemplateVersion | str) -> bool:
        """Return whether this version is strictly newer than another version."""
        return self.compare(other) is TemplateVersionRelation.NEWER

    def __ge__(self, other: TemplateVersion | str) -> bool:
        """Return whether this version is newer than or equal to another version."""
        return self.compare(other) in {
            TemplateVersionRelation.NEWER,
            TemplateVersionRelation.EQUAL,
        }

    def compare(self, other: TemplateVersion | str) -> TemplateVersionRelation:
        """Compare this version to another version.

        A version is newer only when every component is at least as new and at
        least one component is strictly newer. Different plugin sets can still be
        ordered when one set is a superset of the other; divergent sets are
        incomparable.
        """
        other_version = TemplateVersion.parse(other)
        component_names = set(self._components) | set(other_version._components)

        saw_newer = False
        saw_older = False
        for component_name in component_names:
            left_component = self._components.get(component_name)
            right_component = other_version._components.get(component_name)

            if left_component is None:
                saw_older = True
            elif right_component is None:
                saw_newer = True
            else:
                relation = _compare_component_versions(left_component, right_component)
                if relation is TemplateVersionRelation.INCOMPARABLE:
                    return TemplateVersionRelation.INCOMPARABLE
                if relation is TemplateVersionRelation.NEWER:
                    saw_newer = True
                elif relation is TemplateVersionRelation.OLDER:
                    saw_older = True

            if saw_newer and saw_older:
                return TemplateVersionRelation.INCOMPARABLE

        if saw_newer:
            return TemplateVersionRelation.NEWER
        if saw_older:
            return TemplateVersionRelation.OLDER
        return TemplateVersionRelation.EQUAL

    @property
    def _components(self) -> dict[str, str]:
        components = {self.engine_name: self.engine_version}
        components.update({plugin.name: plugin.version for plugin in self.plugins})
        return components


def _entry_points_for_group(group: str) -> Any:
    """Return entry points for a group across supported metadata APIs."""
    discovered_entry_points = entry_points()
    if hasattr(discovered_entry_points, "select"):
        return discovered_entry_points.select(group=group)
    return discovered_entry_points.get(group, [])


def _distribution_version(distribution_name: str) -> str:
    """Return a package version, or a readable fallback for local source runs."""
    try:
        return version(distribution_name)
    except PackageNotFoundError:
        return LOCAL_VERSION


def installed_template_plugins() -> list[tuple[str, str]]:
    """Return installed template plugin package names and versions."""
    plugins = {}
    for entry_point in _entry_points_for_group(PLUGIN_ENTRYPOINT_GROUP):
        distribution = getattr(entry_point, "dist", None)
        if distribution is None:
            plugins[entry_point.name] = "unknown"
            continue

        distribution_name = distribution.metadata.get("Name") or entry_point.name
        plugins[distribution_name] = distribution.version

    return sorted(plugins.items())


def template_version_key() -> str:
    """Return the readable compound template version key for rendered configs."""
    return str(TemplateVersion.current())


def _split_package_version(value: str) -> tuple[str | None, str | None]:
    package_name, separator, package_version = value.rpartition(":")
    if not separator:
        return None, None
    return package_name, package_version


def _compare_component_versions(
    left_version: str,
    right_version: str,
) -> TemplateVersionRelation:
    if left_version == right_version:
        return TemplateVersionRelation.EQUAL

    if _is_sha_build_version(left_version):
        return TemplateVersionRelation.NEWER
    if _is_sha_build_version(right_version):
        return TemplateVersionRelation.OLDER

    try:
        left = Version(left_version)
        right = Version(right_version)
    except InvalidVersion:
        return TemplateVersionRelation.INCOMPARABLE

    if left == right:
        return TemplateVersionRelation.EQUAL
    if left > right:
        return TemplateVersionRelation.NEWER
    return TemplateVersionRelation.OLDER


def _is_sha_build_version(value: str) -> bool:
    """Return whether a version carries CI commit identity as local metadata."""
    return bool(SHA_LOCAL_VERSION_RE.search(value))
