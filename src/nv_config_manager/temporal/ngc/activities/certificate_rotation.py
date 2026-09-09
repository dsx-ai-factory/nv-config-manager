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
"""Activities that reissue and replace device certificates by stable NVUE ID."""

from contextlib import closing
from ipaddress import IPv4Address

from nv_config_manager_dcim import CertificateKind, ZTPDevice
from pydantic import BaseModel
from requests import RequestException
from temporalio import activity
from temporalio.exceptions import ApplicationError

from nv_config_manager.temporal.client.device import CumulusConnection, NetworkConnection
from nv_config_manager.temporal.client.device.exceptions import NetworkDeviceException
from nv_config_manager.temporal.common.mixins.device import NetworkDeviceData, Platform


class RotateDeviceCertificatesInput(BaseModel):
    """Device connectivity and certificate intent needed for one rotation."""

    device_data: NetworkDeviceData
    ztp_device: ZTPDevice


class RotateDeviceCertificatesOutput(BaseModel):
    """Stable IDs successfully replaced on the switch."""

    certificate_ids: tuple[str, ...]


@activity.defn
def rotate_device_certificates(
    activity_input: RotateDeviceCertificatesInput,
) -> RotateDeviceCertificatesOutput:
    """Tell Cumulus to re-fetch each certificate; the endpoint issues fresh PKI data."""
    device = activity_input.device_data
    ztp_device = activity_input.ztp_device
    if device.platform != Platform.CUMULUS_LINUX:
        raise ApplicationError(
            f"Certificate rotation is not implemented for platform {device.platform}",
            non_retryable=True,
        )
    if not ztp_device.ztp_servers:
        raise ApplicationError(
            f"Device {device.name} has no IPv4 ZTP server configured",
            non_retryable=True,
        )
    try:
        ztp_server = str(IPv4Address(ztp_device.ztp_servers[0]))
    except ValueError as exc:
        raise ApplicationError(
            f"Device {device.name} has an invalid IPv4 ZTP server",
            non_retryable=True,
        ) from exc

    rotated: list[str] = []
    failures: list[str] = []
    with closing(NetworkConnection.from_device_data(device)) as connection:
        if not isinstance(connection, CumulusConnection):
            raise ApplicationError(
                f"Device {device.name} did not create a Cumulus connection",
                non_retryable=True,
            )
        for certificate in ztp_device.certificates:
            uri = (
                f"sftp://ztp:ztp@{ztp_server}:2222/device/{ztp_device.device_id}/"
                f"certificates/{certificate.id}"
            )
            extension = "pem" if certificate.kind == CertificateKind.CA else "p12"
            local_path = (
                f"/tmp/nvcm-certificate-{ztp_device.device_id}-{certificate.id}.{extension}"
            )
            failed = False
            try:
                connection.fetch_file(local_path, uri, ztp_device.ztp_vrf)
                connection.import_certificate(
                    certificate.id,
                    certificate.kind,
                    f"file://{local_path}",
                )
            except (NetworkDeviceException, RequestException):
                failed = True
            finally:
                try:
                    connection.delete_file(local_path)
                except (NetworkDeviceException, RequestException):
                    failed = True
            if failed:
                failures.append(certificate.id)
            else:
                rotated.append(certificate.id)
    if failures:
        raise ApplicationError(
            f"Device {device.name} failed to rotate certificate IDs: {', '.join(failures)}"
        )
    return RotateDeviceCertificatesOutput(certificate_ids=tuple(rotated))
