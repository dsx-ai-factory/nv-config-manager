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
"""Tests for provider-neutral device certificate rotation."""

from unittest.mock import MagicMock, patch

from nv_config_manager_dcim import CertificateKind, DeviceCertificate, ZTPDevice

from nv_config_manager.temporal.client.device import CumulusConnection
from nv_config_manager.temporal.common.mixins.device import NetworkDeviceData
from nv_config_manager.temporal.ngc.activities.certificate_rotation import (
    RotateDeviceCertificatesInput,
    rotate_device_certificates,
)


def _network_device() -> NetworkDeviceData:
    return NetworkDeviceData(
        id="device-id",
        name="switch-1",
        role="Leaf",
        platform="cumulus-linux",
        site="site-1",
        device_type="SN5600",
        primary_ip4="192.0.2.20",
        primary_ip6=None,
    )


def _ztp_device() -> ZTPDevice:
    return ZTPDevice(
        device_id="device-id",
        name="switch-1",
        addresses=["192.0.2.20"],
        platform_name="Cumulus Linux",
        firmware_version="5.16.1",
        config_store_instance=None,
        ztp_servers=("192.0.2.10",),
        certificates=(
            DeviceCertificate(id="otel-ca", source="telemetry-ca", kind="ca"),
            DeviceCertificate(id="otel-client", source="telemetry", kind="identity"),
        ),
    )


@patch("nv_config_manager.temporal.ngc.activities.certificate_rotation.NetworkConnection")
def test_rotation_reimports_each_assigned_certificate(mock_network_connection) -> None:
    """The nightly activity reuses stable IDs and the source-IP-authenticated endpoint."""
    connection = CumulusConnection.__new__(CumulusConnection)
    connection.import_certificate = MagicMock()
    connection.close = MagicMock()
    mock_network_connection.from_device_data.return_value = connection

    result = rotate_device_certificates(
        RotateDeviceCertificatesInput(
            device_data=_network_device(),
            ztp_device=_ztp_device(),
        )
    )

    base_uri = "https://192.0.2.10/v1/device/device-id/certificates"
    assert connection.import_certificate.call_args_list == [
        (
            ("otel-ca", CertificateKind.CA, f"{base_uri}/otel-ca"),
            {},
        ),
        (
            ("otel-client", CertificateKind.IDENTITY, f"{base_uri}/otel-client"),
            {},
        ),
    ]
    assert result.certificate_ids == ("otel-ca", "otel-client")
    connection.close.assert_called_once_with()
