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
"""Pydantic model contract tests for the provider-neutral SDK."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from nv_config_manager_dcim import (
    CertificateKind,
    DCIMDeviceSelection,
    DeviceCertificate,
    DeviceMetadata,
    OSImageVersions,
    RenderDeviceIdentity,
    RenderLocation,
    ZTPDevice,
)
from nv_config_manager_dcim.render import DeviceRenderData, LocationRenderData, RenderData


def test_os_image_versions_is_named_and_tuple_compatible() -> None:
    """OS image data retains its established three-value tuple representation."""
    versions = OSImageVersions("5.1.0", "5.2.0", "192.0.2.1")

    assert versions.intended_firmware == "5.1.0"
    assert versions.desired_firmware == "5.2.0"
    assert versions.ztp_address == "192.0.2.1"
    assert tuple(versions) == ("5.1.0", "5.2.0", "192.0.2.1")


def test_sdk_contract_models_are_pydantic_and_immutable() -> None:
    """Provider-neutral value contracts use Pydantic validation and immutability."""
    selection = DCIMDeviceSelection(id="device-1", name="leaf-1")
    render_data = RenderData(
        device=DeviceRenderData(
            identity=RenderDeviceIdentity(
                id="device-1",
                name="leaf-1",
                platform="Cumulus Linux",
                role="Leaf",
                model="SN5600",
                location=RenderLocation(name="site-1", kind="Site"),
            )
        ),
        location=LocationRenderData(location=RenderLocation(name="site-1", kind="Site")),
    )

    assert isinstance(selection, BaseModel)
    assert isinstance(render_data, BaseModel)
    assert render_data.device.identity.name == "leaf-1"
    assert render_data.device.interfaces == ()
    assert render_data.device.network.vrfs == ()
    assert render_data.device.routing.bgp_instances == ()
    assert render_data.location.address_space.prefixes == ()
    assert "inventory" not in DeviceRenderData.model_fields
    assert "intent" not in DeviceRenderData.model_fields
    assert "inventory" not in LocationRenderData.model_fields
    assert "intent" not in LocationRenderData.model_fields
    with pytest.raises(ValidationError):
        selection.name = "leaf-2"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        DCIMDeviceSelection(id="device-1", name="leaf-1", provider_field="not portable")


def test_device_metadata_preserves_mutable_url_and_legacy_alias() -> None:
    """Config-store enrichment can update its URL during the compatibility window."""
    metadata = DeviceMetadata(
        device_id="device-1",
        name="leaf-1",
        site="site-1",
        nautobot_url="https://dcim.example/devices/device-1",
    )

    assert isinstance(metadata, BaseModel)
    assert metadata.device_url == "https://dcim.example/devices/device-1"
    assert metadata.nautobot_url == metadata.device_url
    assert "nautobot_url" not in metadata.to_dict()

    metadata.nautobot_url = "https://dcim.example/devices/device-1/updated"

    assert metadata.device_url == "https://dcim.example/devices/device-1/updated"


def test_ztp_device_certificate_intent_is_typed_and_unique() -> None:
    """ZTP exposes provider-neutral certificate declarations with unique IDs."""
    certificate = DeviceCertificate(
        id="otel-client",
        source="telemetry-client",
        kind=CertificateKind.IDENTITY,
    )
    device = ZTPDevice(
        device_id="device-1",
        name="leaf-1",
        addresses=["192.0.2.10"],
        platform_name="Cumulus Linux",
        firmware_version="5.16.1",
        config_store_instance=None,
        certificates=(certificate,),
    )

    assert device.certificates == (certificate,)
    with pytest.raises(ValidationError, match="certificate IDs must be unique"):
        ZTPDevice(
            device_id="device-1",
            name="leaf-1",
            addresses=["192.0.2.10"],
            platform_name="Cumulus Linux",
            firmware_version="5.16.1",
            config_store_instance=None,
            certificates=(certificate, certificate),
        )


def test_ztp_service_trust_requires_a_ca_certificate() -> None:
    """Only public CA material may bootstrap a named service over HTTP."""
    with pytest.raises(ValidationError, match="only CA certificates"):
        DeviceCertificate(
            id="ztp-identity",
            source="ztp",
            kind="identity",
            services=("ztp",),
        )


@pytest.mark.parametrize("field", ["id", "source"])
def test_device_certificate_rejects_unsafe_identifiers(field: str) -> None:
    """Certificate identifiers are safe to embed in URLs and filenames."""
    values = {"id": "otel-client", "source": "telemetry-client", "kind": "identity"}
    values[field] = "../unsafe"

    with pytest.raises(ValidationError):
        DeviceCertificate.model_validate(values)


def test_render_device_rejects_wrong_otel_certificate_kind() -> None:
    """OTLP CA and client references resolve to correctly typed assignments."""
    with pytest.raises(ValidationError, match="OTLP CA certificate"):
        DeviceRenderData.model_validate(
            {
                "identity": {
                    "id": "device-id",
                    "name": "switch-1",
                    "platform": "Cumulus Linux",
                    "role": "Leaf",
                    "model": "SN5600",
                    "location": {"name": "site-1"},
                },
                "certificates": [{"id": "otel-ca", "source": "telemetry", "kind": "identity"}],
                "telemetry": {
                    "otlp": {
                        "ca_certificate": "otel-ca",
                        "destinations": [
                            {
                                "address": "192.0.2.40",
                                "client_certificate": "otel-ca",
                            }
                        ],
                    }
                },
            }
        )
