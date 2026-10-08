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
"""Tests for portable provider-neutral render-data cache envelopes."""

from __future__ import annotations

import pytest
from nv_config_manager_dcim import (
    DeviceRenderData,
    LocationRenderData,
    RenderData,
    RenderDataExtension,
    RenderDeviceIdentity,
    RenderLocation,
)


def test_render_data_cache_round_trip() -> None:
    """The cache envelope preserves all provider-owned render data."""
    original = RenderData(
        device=DeviceRenderData(
            identity=RenderDeviceIdentity(
                id="device-1",
                name="leaf-1",
                platform="Cumulus Linux",
                role="Leaf",
                model="SN5600",
                location=RenderLocation(id="location-1", name="site-1", kind="Site"),
            )
        ),
        location=LocationRenderData(
            location=RenderLocation(id="location-1", name="site-1", kind="Site")
        ),
        plugin_data={
            "example": RenderDataExtension(
                schema="example.render-data",
                version=1,
                data={"enabled": True},
            )
        },
    )

    restored = RenderData.from_cache(original.to_cache())

    assert restored == original


def test_render_data_cache_rejects_unknown_schema_version() -> None:
    """A cache produced by an incompatible CLI fails with a clear error."""
    with pytest.raises(ValueError, match="schema version"):
        RenderData.from_cache({"schema_version": 99})
