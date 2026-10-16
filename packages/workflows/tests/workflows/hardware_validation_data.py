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
"""Focused hardware-validation workflow test data."""

from nv_config_manager_dcim.workflow_models import NetworkDeviceData, Platform

TEST_DEVICE = NetworkDeviceData(
    id="c8f7a95e-4b2a-4e8c-9d5f-1a2b3c4d5e6f",
    name="test-cumulus-switch",
    role="tor-switch",
    platform=Platform.CUMULUS_LINUX,
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
