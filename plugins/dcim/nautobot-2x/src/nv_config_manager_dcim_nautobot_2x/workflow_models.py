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
"""Nautobot-native conversion for provider-neutral workflow models."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import netaddr
from nv_config_manager_dcim.errors import DCIMInvalidDataError
from nv_config_manager_dcim.workflow_models import (
    DeviceBayData,
    HostDeviceData,
    InterfaceData,
    NetworkDeviceData,
    Platform,
)


def _slugify(name: str) -> str:
    """Map one Nautobot display name to the SDK's stable slug."""
    return name.strip().lower().replace(" ", "-")


def _require_str(data: Mapping[str, Any], key: str, label: str) -> str:
    """Return a required non-empty string from a Nautobot response."""
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise DCIMInvalidDataError(f"{label} is missing required field '{key}'")
    return value


def _extract_site(device: dict[str, Any]) -> str:
    """Find the Site record in a Nautobot location hierarchy."""
    location = device.get("location")
    while location:
        if (location.get("location_type") or {}).get("name") == "Site":
            return _require_str(location, "name", "Site location")
        location = location.get("parent")
    raise DCIMInvalidDataError(f"Could not identify a site for {device.get('name')}")


def device_bay_from_nautobot_graphql(bay: dict[str, Any]) -> DeviceBayData:
    """Translate one Nautobot device-bay response."""
    installed = bay.get("installed_device") or {}
    return DeviceBayData(
        id=str(bay["id"]),
        name=str(bay["name"]),
        installed_device_id=installed.get("id"),
    )


def interface_from_nautobot_graphql(interface: dict[str, Any]) -> InterfaceData:
    """Translate one Nautobot interface response."""
    device = interface.get("device") or (interface.get("module") or {}).get("device")
    if not device:
        raise DCIMInvalidDataError(f"Interface {interface.get('id')} has no device associated")
    mac_address = interface.get("mac_address")
    vrf = interface.get("vrf") or {}
    try:
        parsed_mac_address = str(netaddr.EUI(mac_address)) if mac_address else None
    except netaddr.core.AddrFormatError as exc:
        raise DCIMInvalidDataError(
            f"Interface {interface.get('name')} has invalid MAC address: {mac_address}"
        ) from exc
    return InterfaceData(
        id=str(interface["id"]),
        name=str(interface["name"]),
        host=str(device.get("name") or device.get("id")),
        mac_address=parsed_mac_address,
        vrf_id=vrf.get("id"),
    )


def network_device_from_nautobot_graphql(device: dict[str, Any]) -> NetworkDeviceData:
    """Translate one Nautobot network-device response."""
    status = device.get("configmanagerdevicestatus") or {}
    platform = device.get("platform") or {}
    role = device.get("role") or {}
    device_type = device.get("device_type") or {}
    rack = device.get("rack") or {}
    primary_ip4 = device.get("primary_ip4") or {}
    primary_ip6 = device.get("primary_ip6") or {}
    return NetworkDeviceData(
        id=_require_str(device, "id", "Network device"),
        name=_require_str(device, "name", "Network device"),
        rack=rack.get("name"),
        position=device.get("position"),
        role=_slugify(_require_str(role, "name", "Network device role")),
        platform=Platform(_slugify(_require_str(platform, "name", "Network device platform"))),
        device_type=_slugify(_require_str(device_type, "model", "Network device type")),
        site=_extract_site(device),
        primary_ip4=primary_ip4.get("host"),
        primary_ip6=primary_ip6.get("host"),
        intent=device.get("config_context"),
        render_enabled=bool(status.get("render_enabled", False)),
        deploy_enabled=bool(status.get("deploy_enabled", False)),
        backup_enabled=bool(status.get("backup_enabled", False)),
        ztp_enabled=bool(status.get("ztp_enabled", False)),
    )


def host_device_from_nautobot_graphql(device: dict[str, Any]) -> HostDeviceData:
    """Translate one Nautobot host-device response."""
    role = device.get("role") or {}
    device_type = device.get("device_type") or {}
    rack = device.get("rack") or {}
    return HostDeviceData(
        id=_require_str(device, "id", "Host device"),
        name=_require_str(device, "name", "Host device"),
        rack=rack.get("name"),
        position=device.get("position"),
        role=_slugify(_require_str(role, "name", "Host device role")),
        device_type=_slugify(_require_str(device_type, "model", "Host device type")),
        site=_extract_site(device),
        device_bays=[
            device_bay_from_nautobot_graphql(item) for item in device.get("device_bays", [])
        ],
        interfaces=[interface_from_nautobot_graphql(item) for item in device.get("interfaces", [])],
        serial=device.get("serial"),
    )
