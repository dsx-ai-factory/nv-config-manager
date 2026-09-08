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
"""Shared aioresponses stubs for IB PKey member workflow tests."""

from aioresponses import aioresponses

NB_API = "https://nautobot.example.com/api"
NB_GRAPHQL = f"{NB_API}/graphql/"

DEFAULT_RESOLVED_DEVICE_ID = "ufm-dev-001"
DEFAULT_RESOLVED_LOCATION_ID = "loc-001"
DEFAULT_RESOLVED_LOCATION_NAME = "test-site"
DEFAULT_RESOLVED_PKEY_ID = "pkey-rec-001"


def stub_graphql_resolve_guids(m: aioresponses, guid_to_iface: list[tuple]) -> None:
    """Stub the GraphQL batched reverse-lookup of GUIDs to Nautobot interfaces."""
    m.post(
        NB_GRAPHQL,
        payload={
            "data": {
                "interfaces": [
                    {
                        "id": iface_uuid,
                        "name": "mlx5_0",
                        "cf_ib_guid": guid,
                        "device": {"name": "hca01"},
                    }
                    for guid, iface_uuid in guid_to_iface
                ]
            }
        },
    )


def stub_graphql_resolve_ib_context(
    m: aioresponses,
    *,
    host: str = "ufm.example.com",
    pkey: str = "0x0005",
    overlay_id: str,
    overlay_name: str = "ib-pkey-overlay",
    location_name: str = DEFAULT_RESOLVED_LOCATION_NAME,
    location_id: str = DEFAULT_RESOLVED_LOCATION_ID,
    device_id: str = DEFAULT_RESOLVED_DEVICE_ID,
    pkey_id: str = DEFAULT_RESOLVED_PKEY_ID,
) -> None:
    """Stub the GraphQL lookup for resolve_ib_context (name-based device path)."""
    m.post(
        NB_GRAPHQL,
        payload={
            "data": {
                "devices": [
                    {
                        "id": device_id,
                        "name": host,
                        "role": {"name": "UFM"},
                        "primary_ip4": {"host": "10.0.0.1"},
                        "location": {
                            "id": location_id,
                            "name": location_name,
                            "location_type": {"name": "Site"},
                            "overlays": [
                                {
                                    "id": overlay_id,
                                    "name": overlay_name,
                                    "pkeys": [{"id": pkey_id, "pkey": pkey}],
                                }
                            ],
                        },
                    }
                ]
            }
        },
    )
