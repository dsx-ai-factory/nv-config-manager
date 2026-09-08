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
"""Location filter tests."""

import pytest
from nv_config_manager_dcim import LocationRenderData, RenderLocation

from nv_config_manager_templates.filters import FilterException
from nv_config_manager_templates.filters.location import location_has_tag, site_aggregates


def test_site_aggregates(public_location_data: dict) -> None:
    """Site aggregates are loaded by role and can fail or return empty."""
    assert site_aggregates(public_location_data, "Site-Aggregate") == ["10.78.144.0/20"]
    assert site_aggregates(public_location_data, "NONEXISTENT", fail_if_missing=False) == []

    with pytest.raises(FilterException, match="Found no aggregates in role 'NONEXISTENT'"):
        site_aggregates(public_location_data, "NONEXISTENT")


def test_location_has_tag() -> None:
    """Location tag helper handles missing and present location tags."""
    assert (
        location_has_tag(
            LocationRenderData(location=RenderLocation(name="TEST-SITE", kind="Site")),
            "non-forge-managed",
        )
        is False
    )
    assert (
        location_has_tag(
            LocationRenderData(
                location=RenderLocation(name="TEST-SITE", kind="Site", tags=("non-forge-managed",))
            ),
            "non-forge-managed",
        )
        is True
    )
    assert (
        location_has_tag(
            LocationRenderData(
                location=RenderLocation(name="TEST-SITE", kind="Site", tags=("non-forge-managed",))
            ),
            "missing",
        )
        is False
    )
