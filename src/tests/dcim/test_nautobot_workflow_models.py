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
"""Tests for Nautobot workflow-model normalization."""

import pytest
from nv_config_manager_dcim.errors import DCIMInvalidDataError
from nv_config_manager_dcim_nautobot_2x.workflow_models import (
    interface_from_nautobot_graphql,
    network_device_from_nautobot_graphql,
)


def _network_device() -> dict[str, object]:
    return {
        "id": "device-id",
        "name": "leaf-1",
        "role": {"name": " Network Device "},
        "platform": {"name": " Cumulus Linux "},
        "device_type": {"model": " Spectrum 4 "},
        "location": {"name": "site-1", "location_type": {"name": "Site"}},
    }


def test_network_device_required_names_are_validated() -> None:
    """Null required strings fail within the provider error contract."""
    device = _network_device()
    device["role"] = {"name": None}

    with pytest.raises(DCIMInvalidDataError, match="Network device role.*'name'"):
        network_device_from_nautobot_graphql(device)


def test_site_name_is_required() -> None:
    """A Site record with no name cannot become the string 'None'."""
    device = _network_device()
    device["location"] = {"name": None, "location_type": {"name": "Site"}}

    with pytest.raises(DCIMInvalidDataError, match="Site location.*'name'"):
        network_device_from_nautobot_graphql(device)


def test_display_names_are_trimmed_before_slugification() -> None:
    """Provider slugs do not gain hyphens from surrounding whitespace."""
    normalized = network_device_from_nautobot_graphql(_network_device())

    assert normalized.role == "network-device"
    assert normalized.platform == "cumulus-linux"
    assert normalized.device_type == "spectrum-4"


def test_invalid_interface_mac_uses_provider_error_contract() -> None:
    """Third-party MAC parse errors do not escape the provider boundary."""
    interface = {
        "id": "interface-id",
        "name": "eth0",
        "device": {"id": "device-id", "name": "leaf-1"},
        "mac_address": "not-a-mac",
    }

    with pytest.raises(DCIMInvalidDataError, match="eth0 has invalid MAC address"):
        interface_from_nautobot_graphql(interface)
