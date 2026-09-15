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
"""Tests for template version comparison."""

from nv_config_manager_templates.version import TemplateVersion, TemplateVersionRelation


def test_template_version_string_round_trip() -> None:
    """Test readable compound template version formatting."""
    version = TemplateVersion.parse(
        "engine=nv-config-manager-templates:1.2.3;"
        "plugins=nv-config-manager-dgxc-templates:2.0.0,nv-config-manager-azure-templates:1.0.0"
    )

    assert str(version) == (
        "engine=nv-config-manager-templates:1.2.3;"
        "plugins=nv-config-manager-azure-templates:1.0.0,nv-config-manager-dgxc-templates:2.0.0"
    )


def test_template_version_compares_engine_versions() -> None:
    """Test legacy and compound engine version ordering."""
    assert TemplateVersion.parse("1.2.0") > TemplateVersion.parse("1.1.0")
    assert TemplateVersion.parse(
        "engine=nv-config-manager-templates:1.2.0;plugins=none"
    ) > TemplateVersion.parse("engine=nv-config-manager-templates:1.1.0;plugins=none")


def test_template_version_treats_pep440_normalized_versions_as_equal() -> None:
    """Test Git tag and Python package RC spellings compare equally."""
    git_tag_version = TemplateVersion.parse(
        "engine=nv-config-manager-templates:1.2.1-rc.72;plugins=none"
    )
    package_version = TemplateVersion.parse(
        "engine=nv-config-manager-templates:1.2.1rc72;plugins=none"
    )

    assert git_tag_version == package_version
    assert git_tag_version.compare(package_version) is TemplateVersionRelation.EQUAL


def test_template_version_treats_sha_build_as_newer() -> None:
    """Test CI SHA-local versions force template refreshes in deploy-test."""
    current = TemplateVersion.parse(
        "engine=nv-config-manager-templates:1.2.2rc22+g24105c49;plugins=none"
    )

    assert current > TemplateVersion.parse("engine=nv-config-manager-templates:1.2.2rc22")
    assert current > TemplateVersion.parse("engine=nv-config-manager-templates:1.2.2rc22+gffffffff")
    assert current > TemplateVersion.parse("0.3.50")
    assert current.compare(current) is TemplateVersionRelation.EQUAL


def test_template_version_compares_plugin_versions() -> None:
    """Test plugin version ordering is part of the version vector."""
    current = TemplateVersion.parse(
        "engine=nv-config-manager-templates:1.2.0;plugins=nv-config-manager-azure-templates:1.1.0"
    )
    rendered = TemplateVersion.parse(
        "engine=nv-config-manager-templates:1.2.0;plugins=nv-config-manager-azure-templates:1.0.0"
    )

    assert current > rendered


def test_template_version_allows_plugin_superset_as_newer() -> None:
    """Test adding a plugin makes the current vector newer."""
    current = TemplateVersion.parse(
        "engine=nv-config-manager-templates:1.2.0;plugins=nv-config-manager-azure-templates:1.0.0"
    )
    rendered = TemplateVersion.parse("engine=nv-config-manager-templates:1.2.0;plugins=none")

    assert current > rendered


def test_template_version_treats_divergent_plugins_as_incomparable() -> None:
    """Test mixed newer/older component sets do not compare as newer."""
    current = TemplateVersion.parse(
        "engine=nv-config-manager-templates:1.2.0;plugins=nv-config-manager-azure-templates:1.0.0"
    )
    rendered = TemplateVersion.parse(
        "engine=nv-config-manager-templates:1.2.0;plugins=nv-config-manager-dgxc-templates:1.0.0"
    )

    assert current.compare(rendered) is TemplateVersionRelation.INCOMPARABLE
    assert not current > rendered
