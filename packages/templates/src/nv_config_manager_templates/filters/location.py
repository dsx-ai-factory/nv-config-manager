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
"""Location data filters."""

from __future__ import annotations

import ipaddress

from nv_config_manager_dcim import LocationRenderData

from nv_config_manager_templates.filters import FilterException


def site_aggregates(
    value: LocationRenderData,
    role_name: str,
    tags: set[str] | list[str] | None = None,
    exclude_tags: set[str] | list[str] | None = None,
    fail_if_missing: bool = True,
) -> list[str]:
    """Return the site level aggregates by name."""
    aggregates = []

    for prefix_entry in value.address_space.prefixes:
        prefix_tags = set(prefix_entry.tags)

        # Check if prefix should be excluded
        if exclude_tags and set(exclude_tags).intersection(prefix_tags):
            continue

        if (
            prefix_entry.role
            and prefix_entry.role.lower() == role_name.lower()
            and (not tags or set(tags).issubset(prefix_tags))
        ):
            aggregates.append(str(prefix_entry.prefix))
    if not aggregates:
        if not fail_if_missing:
            return []
        exception_message = f"Found no aggregates in role '{role_name}'"
        if tags:
            exception_message += f" tagged with tags {tags}"

        raise FilterException(exception_message)

    # Dedup for cases where we have duplicate prefixes across CIN fabrics
    return sorted(set(aggregates), key=ipaddress.ip_network)


def location_has_tag(value: LocationRenderData, tag_name: str) -> bool:
    """Return true if the location has a specific tag."""
    return tag_name in value.location.tags
