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
"""Contract tests for externally installable DCIM providers."""

from __future__ import annotations

import pytest

from nv_config_manager_dcim import (
    DCIMProviderConfigurationError,
    DCIMProviderMetadata,
    create_dcim_client,
    registry,
)


class SampleClient:
    """Small provider client used to test discovery construction only."""

    async def close(self) -> None:
        """Release no resources."""


class SampleProvider:
    """Provider author fixture with no dependency on NVCM core."""

    metadata = DCIMProviderMetadata(
        name="sample-dcim",
        display_name="Sample DCIM",
        provider_version="0.1.0",
        supported_api_versions=("1.0",),
    )

    def validate_settings(self, settings: dict[str, object]) -> None:
        """Require an explicit endpoint setting."""
        if settings.get("endpoint") != "https://dcim.example":
            raise DCIMProviderConfigurationError("sample endpoint is required")

    def create_client(self, settings: dict[str, object]) -> SampleClient:
        """Return the provider-owned client."""
        return SampleClient()


class FakeEntryPoint:
    """Minimal importlib metadata entry point."""

    name = "sample-dcim"

    def load(self):
        """Return the provider factory."""
        return SampleProvider


def test_external_provider_uses_explicit_settings(monkeypatch) -> None:
    """A provider can be discovered and tested without NVCM core installed."""
    monkeypatch.setattr(registry, "_entry_points_for_group", lambda: (FakeEntryPoint(),))

    client = create_dcim_client("sample-dcim", {"endpoint": "https://dcim.example"})

    assert isinstance(client, SampleClient)


def test_external_provider_validates_its_own_settings(monkeypatch) -> None:
    """Provider validation errors preserve SDK error semantics."""
    monkeypatch.setattr(registry, "_entry_points_for_group", lambda: (FakeEntryPoint(),))

    with pytest.raises(DCIMProviderConfigurationError, match="sample endpoint"):
        create_dcim_client("sample-dcim", {})
