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
"""Option sources that several built-in workflow forms share.

They load from the workflow API's ``/v1/parameter`` endpoints with the query
parameters the legacy workflow forms sent.
"""

from nv_config_manager_workflows.ui import Dependency, OptionSource

LOCATION_SOURCE = OptionSource(
    "/v1/parameter/location",
    "name",
    "id",
    type_key="location_type",
    params={"location_type": ["Site", "Module"]},
)
"""Site and module locations: labelled by name, valued by id, typed by location type."""

MANAGED_DEVICE_SOURCE = OptionSource(
    "/v1/parameter/device", "name", "id", params={"managed_only": True}
)
"""Managed network devices: labelled by name, valued by id."""

MANAGED_ROLE_SOURCE = OptionSource(
    "/v1/parameter/role", "name", "name", params={"managed_only": True}
)
"""Roles assigned to managed devices, by name."""

MANAGED_TENANT_SOURCE = OptionSource(
    "/v1/parameter/tenant", "name", "name", params={"managed_only": True}
)
"""Tenants that own managed devices, by name."""

DEVICE_STATUS_SOURCE = OptionSource(
    "/v1/parameter/status", "name", "name", params={"content_type": "dcim.device"}
)
"""Device statuses, by name."""

NAMESPACE_TAG_SOURCE = OptionSource(
    "/v1/parameter/namespace-tag",
    "name",
    "name",
    depends_on={
        "location": Dependency("site", required=False),
        "location_type": Dependency("site_type", required=False),
    },
)
"""Namespace tags, optionally scoped to the form's ``site`` and ``site_type``.

No ``clear_on_change``: choosing a site keeps the default or chosen tag, as the
legacy forms did. Clearing it would omit the field, so the workflow would
silently run with the default tag instead of the one shown.
"""

__all__ = [
    "DEVICE_STATUS_SOURCE",
    "LOCATION_SOURCE",
    "MANAGED_DEVICE_SOURCE",
    "MANAGED_ROLE_SOURCE",
    "MANAGED_TENANT_SOURCE",
    "NAMESPACE_TAG_SOURCE",
]
