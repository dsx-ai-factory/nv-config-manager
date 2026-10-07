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
from typing import ClassVar
from unittest.mock import patch

import pytest

from nv_config_manager_installer.providers import registry
from nv_config_manager_installer.providers.api import (
    DCIMInstallerProvider,
    InstallerProviderMetadata,
)


class SyntheticInstallerProvider(DCIMInstallerProvider):
    """External-only provider with no contributions, used to exercise generic paths."""

    metadata: ClassVar[InstallerProviderMetadata] = InstallerProviderMetadata(
        name="synthetic",
        display_name="Synthetic DCIM",
        supports_local=False,
        event_stream="synthetic-dcim",
        event_subject="synthetic.change",
    )


class _FakeEntryPoint:
    def __init__(self, name: str, target: type[DCIMInstallerProvider]) -> None:
        self.name = name
        self._target = target

    def load(self) -> type[DCIMInstallerProvider]:
        return self._target


@pytest.fixture(autouse=True)
def _synthetic_provider(monkeypatch: pytest.MonkeyPatch):
    """Register the synthetic provider alongside the installed entry points."""
    real = registry._entry_points_for_group

    def entry_points():
        return (*real(), _FakeEntryPoint("synthetic", SyntheticInstallerProvider))

    monkeypatch.setattr(registry, "_entry_points_for_group", entry_points)
    yield


@pytest.fixture(autouse=True)
def _skip_inotify_check():
    with patch("nv_config_manager_installer.deployer.Deployer._check_inotify_limits"):
        yield
