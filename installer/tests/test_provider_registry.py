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
"""Provider discovery, mode support, and local-only secret groups."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from nv_config_manager_installer.cli import main
from nv_config_manager_installer.providers import registry
from nv_config_manager_installer.providers.errors import (
    InstallerProviderConfigurationError,
    InstallerProviderDiscoveryError,
    InstallerProviderDuplicateError,
)
from nv_config_manager_installer.providers.nautobot import NautobotInstallerProvider
from nv_config_manager_installer.schema import (
    BUILT_IN_NAUTOBOT_PROVIDER,
    NVConfigManagerInstallConfig,
)
from nv_config_manager_installer.secrets import build_openbao_secret_data


class _EntryPoint:
    def __init__(self, name: str, target: object) -> None:
        self.name = name
        self._target = target
        self.loads = 0

    def load(self) -> object:
        self.loads += 1
        if isinstance(self._target, Exception):
            raise self._target
        return self._target


@pytest.fixture
def entry_points(monkeypatch: pytest.MonkeyPatch) -> list[_EntryPoint]:
    points = [_EntryPoint(BUILT_IN_NAUTOBOT_PROVIDER, NautobotInstallerProvider)]
    monkeypatch.setattr(registry, "_entry_points_for_group", lambda: tuple(points))
    monkeypatch.setattr(registry, "_LOADED", {})
    return points


def test_broken_provider_does_not_hide_the_others(entry_points: list[_EntryPoint]) -> None:
    entry_points.append(_EntryPoint("broken", ImportError("No module named 'gone'")))

    assert list(registry.discover_installer_providers()) == [BUILT_IN_NAUTOBOT_PROVIDER]
    assert registry.get_installer_provider(BUILT_IN_NAUTOBOT_PROVIDER).metadata.name == (
        BUILT_IN_NAUTOBOT_PROVIDER
    )
    with pytest.raises(InstallerProviderDiscoveryError, match="No module named 'gone'"):
        registry.get_installer_provider("broken")
    assert set(registry.installer_provider_errors()) == {"broken"}


def test_duplicate_names_disable_only_that_provider(entry_points: list[_EntryPoint]) -> None:
    entry_points.append(_EntryPoint("twice", NautobotInstallerProvider))
    entry_points.append(_EntryPoint("twice", NautobotInstallerProvider))

    assert list(registry.discover_installer_providers()) == [BUILT_IN_NAUTOBOT_PROVIDER]
    with pytest.raises(InstallerProviderDuplicateError):
        registry.get_installer_provider("twice")


def test_providers_are_loaded_once(entry_points: list[_EntryPoint]) -> None:
    first = registry.get_installer_provider(BUILT_IN_NAUTOBOT_PROVIDER)
    second = registry.get_installer_provider(BUILT_IN_NAUTOBOT_PROVIDER)

    assert first is second
    assert entry_points[0].loads == 1


def test_providers_list_reports_unavailable_providers(entry_points: list[_EntryPoint]) -> None:
    entry_points.append(_EntryPoint("broken", ImportError("No module named 'gone'")))

    result = CliRunner().invoke(main, ["providers", "list"])

    assert result.exit_code == 0
    assert BUILT_IN_NAUTOBOT_PROVIDER in result.stdout
    assert "broken" in result.stderr
    assert "unavailable" in result.stderr


class _LocalOnlyProvider(NautobotInstallerProvider):
    """Nautobot whose OpenBao groups always include the local-only admin group."""

    def openbao_groups(self, config, value_of):  # type: ignore[no-untyped-def]
        groups = super().openbao_groups(config, value_of)
        groups.setdefault("nautobot_app", {"adminPassword": "x"})
        return groups


def test_local_mode_is_rejected_for_an_external_only_provider() -> None:
    config = NVConfigManagerInstallConfig.model_validate(
        {"dcim": {"provider": "synthetic", "local": True}}
    )

    with pytest.raises(InstallerProviderConfigurationError, match="cannot be deployed in the"):
        registry.resolve_provider(config)


def test_local_only_groups_get_no_vault_values_when_external() -> None:
    config = NVConfigManagerInstallConfig.model_validate(
        {"dcim": {"local": False, "server": "https://nautobot.example.com"}}
    )

    groups = build_openbao_secret_data(config, _LocalOnlyProvider())

    assert "nautobot" in groups
    assert "nautobot_app" not in groups
