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
"""Cumulus hardware endpoint integration test data."""

from nv_config_manager.temporal.common.mixins.device import NetworkDeviceData

TEST_DEVICE = NetworkDeviceData(
    id="c8f7a95e-4b2a-4e8c-9d5f-1a2b3c4d5e6f",
    name="test-cumulus-switch",
    role="tor-switch",
    platform="cumulus-linux",
    site="SITEA",
    device_type="sn5600",
    primary_ip4="192.0.2.100",
    primary_ip6=None,
)

PLATFORM_RESPONSE = {
    "asic-model": "Spectrum-4",
    "cpu": "x86_64 Intel(R) Xeon(R) E-2276ME  CPU @ 2.80GHz x12",
    "disk-size": "149.1GB",
    "manufacturer": "Nvidia",
    "memory": "30.92 GB",
    "part-number": "920-9N42F-00RI-7C0",
    "port-layout": "64 x 800G-OSFP & 1 x 25G-SFP28",
    "product-name": "SN5600",
    "serial-number": "MT2438J01F91",
    "system-mac": "b0:cf:0e:ae:8d:ff",
    "system-uuid": "98942a7c-7b1f-11ef-8000-b0cf0eae8c00",
}

FAN_RESPONSE = {
    "FAN1/1": {
        "current-speed": "7202",
        "direction": "F2B",
        "max-speed": "13800",
        "min-speed": "2800",
        "state": "ok",
    },
    "FAN1/2": {
        "current-speed": "6496",
        "direction": "F2B",
        "max-speed": "13800",
        "min-speed": "2800",
        "state": "ok",
    },
    "FAN2/1": {
        "current-speed": "7281",
        "direction": "F2B",
        "max-speed": "13800",
        "min-speed": "2800",
        "state": "ok",
    },
    "FAN2/2": {
        "current-speed": "6496",
        "direction": "F2B",
        "max-speed": "13800",
        "min-speed": "2800",
        "state": "ok",
    },
    "FAN3/1": {
        "current-speed": "7202",
        "direction": "F2B",
        "max-speed": "13800",
        "min-speed": "2800",
        "state": "ok",
    },
    "FAN3/2": {
        "current-speed": "6560",
        "direction": "F2B",
        "max-speed": "13800",
        "min-speed": "2800",
        "state": "ok",
    },
    "FAN4/1": {
        "current-speed": "7281",
        "direction": "F2B",
        "max-speed": "13800",
        "min-speed": "2800",
        "state": "ok",
    },
    "FAN4/2": {
        "current-speed": "6692",
        "direction": "F2B",
        "max-speed": "13800",
        "min-speed": "2800",
        "state": "ok",
    },
    "PSU1/FAN": {
        "current-speed": "19488",
        "direction": "F2B",
        "max-speed": "32500",
        "min-speed": "9500",
        "state": "ok",
    },
    "PSU2/FAN": {
        "current-speed": "19488",
        "direction": "F2B",
        "max-speed": "32500",
        "min-speed": "9500",
        "state": "ok",
    },
}

LED_RESPONSE = {
    "FAN1": {"color": "green"},
    "FAN2": {"color": "green"},
    "FAN3": {"color": "green"},
    "FAN4": {"color": "green"},
    "PSU": {"color": "green"},
    "SYSTEM": {"color": "green"},
}

TEMPERATURE_RESPONSE = {
    "PSU1": {
        "capacity": 3300.0,
        "current": 7.468,
        "power": 404.0,
        "state": "ok",
        "voltage": 238.75,
    },
    "PSU2": {
        "capacity": 3300.0,
        "current": 7.359,
        "power": 398.0,
        "state": "ok",
        "voltage": 238.25,
    },
}

VOLTAGE_RESPONSE = {
    "ADAPTER": {"state": "ok"},
    "IBC-1-13V5-RAIL-OUT": {"actual": 13.46, "max": 16.0, "min": 8.2, "state": "ok"},
    "IBC-1-PWR-CONV-54V-RAIL-IN1": {"actual": 54.25, "max": 64.0, "min": 35.56, "state": "ok"},
    "IBC-2-13V5-RAIL-OUT": {"actual": 13.46, "max": 16.0, "min": 8.2, "state": "ok"},
    "IBC-2-PWR-CONV-54V-RAIL-IN1": {"actual": 54.19, "max": 64.0, "min": 35.56, "state": "ok"},
    "IBC-3-13V5-RAIL-OUT": {"actual": 13.46, "max": 16.0, "min": 8.2, "state": "ok"},
    "IBC-3-PWR-CONV-54V-RAIL-IN1": {"actual": 54.12, "max": 64.0, "min": 35.56, "state": "ok"},
    "IBC-4-13V5-RAIL-OUT": {"actual": 13.45, "max": 16.0, "min": 8.2, "state": "ok"},
    "IBC-4-PWR-CONV-54V-RAIL-IN1": {"actual": 54.25, "max": 64.0, "min": 35.56, "state": "ok"},
    "PMIC-1-PSU-13V5-RAIL-IN1": {"actual": 13.5, "max": 16.0, "state": "ok"},
    "PMIC-10-HVDD_T03-1V2-RAIL-OUT1": {"actual": 1.2, "max": 1.42, "min": 0.95, "state": "ok"},
    "PMIC-10-HVDD_T47-1V2-RAIL-OUT2": {"actual": 1.2, "max": 1.42, "min": 0.95, "state": "ok"},
    "PMIC-10-PSU-13V5-RAIL-IN1": {"actual": 13.25, "max": 16.0, "state": "ok"},
    "PMIC-11-PSU-13V5-RAIL-IN1": {"actual": 13.25, "max": 16.0, "state": "ok"},
    "PMIC-11-VDDSCC-0V75-RAIL-OUT1": {"actual": 0.751, "max": 0.96, "min": 0.6, "state": "ok"},
    "PMIC-12-COMEX-VCCSA-OUT2": {"actual": 0.92, "max": 1.32, "min": 0.52, "state": "ok"},
    "PMIC-12-COMEX-VCORE-OUT1": {"actual": 0.695, "max": 1.7, "min": 0.9, "state": "failed"},
    "PMIC-12-PSU-13V5-RAIL-VIN": {"actual": 13.25, "max": 15.0, "state": "ok"},
    "PMIC-2-PSU-13V5-RAIL-IN1": {"actual": 13.25, "max": 16.0, "state": "ok"},
    "PMIC-3-PSU-13V5-RAIL-IN1": {"actual": 13.25, "max": 16.0, "state": "ok"},
    "PMIC-4-PSU-13V5-RAIL-IN1": {"actual": 13.25, "max": 16.0, "state": "ok"},
    "PMIC-5-PSU-13V5-RAIL-IN1": {"actual": 13.25, "max": 16.0, "state": "ok"},
    "PMIC-6-PSU-13V5-RAIL-IN1": {"actual": 13.25, "max": 16.0, "state": "ok"},
    "PMIC-7-PSU-13V5-RAIL-IN1": {"actual": 13.25, "max": 16.0, "state": "ok"},
    "PMIC-8-PSU-13V5-RAIL-IN1": {"actual": 13.25, "max": 16.0, "state": "ok"},
    "PMIC-9-PSU-13V5-RAIL-IN1": {"actual": 13.5, "max": 16.0, "state": "ok"},
    "PSU-1L-220V-RAIL-IN": {"actual": 238.5, "max": 280.0, "min": 85.0, "state": "ok"},
    "PSU-1L-54V-RAIL-OUT": {"actual": 54.01, "state": "ok"},
    "PSU-2R-220V-RAIL-IN": {"actual": 238.25, "max": 280.0, "min": 85.0, "state": "ok"},
    "PSU-2R-54V-RAIL-OUT": {"actual": 54.11, "state": "ok"},
}

INVENTORY_RESPONSE = {
    "FAN1/1": {
        "hardware-version": "N/A",
        "model": "N/A",
        "serial": "N/A",
        "state": "ok",
        "type": "fan",
    },
    "FAN1/2": {
        "hardware-version": "N/A",
        "model": "N/A",
        "serial": "N/A",
        "state": "ok",
        "type": "fan",
    },
    "FAN2/1": {
        "hardware-version": "N/A",
        "model": "N/A",
        "serial": "N/A",
        "state": "ok",
        "type": "fan",
    },
    "FAN2/2": {
        "hardware-version": "N/A",
        "model": "N/A",
        "serial": "N/A",
        "state": "ok",
        "type": "fan",
    },
    "FAN3/1": {
        "hardware-version": "N/A",
        "model": "N/A",
        "serial": "N/A",
        "state": "ok",
        "type": "fan",
    },
    "FAN3/2": {
        "hardware-version": "N/A",
        "model": "N/A",
        "serial": "N/A",
        "state": "ok",
        "type": "fan",
    },
    "FAN4/1": {
        "hardware-version": "N/A",
        "model": "N/A",
        "serial": "N/A",
        "state": "ok",
        "type": "fan",
    },
    "FAN4/2": {
        "hardware-version": "N/A",
        "model": "N/A",
        "serial": "N/A",
        "state": "ok",
        "type": "fan",
    },
    "PSU1": {
        "hardware-version": "A4",
        "model": "930-9SPSU-00RA-00B",
        "serial": "MT2439J00U95",
        "state": "ok",
        "type": "psu",
    },
    "PSU1/FAN": {
        "hardware-version": "N/A",
        "model": "N/A",
        "serial": "N/A",
        "state": "ok",
        "type": "fan",
    },
    "PSU2": {
        "hardware-version": "A4",
        "model": "930-9SPSU-00RA-00B",
        "serial": "MT2439J00U3J",
        "state": "ok",
        "type": "psu",
    },
    "PSU2/FAN": {
        "hardware-version": "N/A",
        "model": "N/A",
        "serial": "N/A",
        "state": "ok",
        "type": "fan",
    },
    "SWITCH": {
        "hardware-version": "A9",
        "model": "920-9N42F-00RI-7C0",
        "serial": "MT2438J01F91",
        "state": "ok",
        "type": "switch",
    },
}

API_ERROR_RESPONSE = {
    "error": "Internal server error",
    "message": "Unable to retrieve platform information",
}

API_TIMEOUT_RESPONSE = {"error": "Request timeout", "message": "API request timed out"}
