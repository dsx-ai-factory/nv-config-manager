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
"""V1 Device API Endpoints."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from nv_config_manager.common.auth import auth_required, require_sso_or_device
from nv_config_manager.common.client import ConfigStoreException, ConfigStoreFileNotFound
from nv_config_manager.common.config import get_storage_client, temporal_client
from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.dcim import DCIMNotFoundError, dcim_client_session
from nv_config_manager.ztp.api.schemas import ChecksumResponse
from nv_config_manager.ztp.api.streaming import create_object_storage_streaming_response
from nv_config_manager.ztp.device import DeviceData
from nv_config_manager.ztp.storage import ObjectStorageNotFoundException

logger = get_logger(__name__, category=LogCategory.ZTP_API)

router = APIRouter(prefix="/device", tags=["device"], responses={404: {"description": "Not found"}})


async def _get_device_data(device_uuid: str) -> DeviceData:
    """Load ZTP device data through the selected DCIM provider."""
    async with dcim_client_session() as client:
        return DeviceData.from_dcim(await client.get_ztp_device(device_uuid))


async def _authorize_request(request: Request, device_uuid: str) -> None:
    # This endpoint has sensitive content, check if coming from the
    # device associated with this configuration

    if not auth_required():
        return

    identity = await require_sso_or_device(request)
    if identity is not None and identity.source != "anonymous":
        # Request came in through SSO, mTLS, SPIFFE, or JWT/OIDC — the
        # shared auth layer has validated it, so no further IP check needed.
        return

    try:
        device_data = await _get_device_data(device_uuid)
    except DCIMNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    allowed_addresses = device_data.addresses
    allowed_addresses.append("127.0.0.1")

    if request.client is None:
        raise HTTPException(status_code=403, detail="Unable to determine client IP address.")

    client_ip = request.client.host

    if client_ip not in allowed_addresses:
        logger.warning(
            "Unauthorized ZTP request for device %s from %s (allowed: %s)",
            device_uuid,
            client_ip,
            allowed_addresses,
        )
        raise HTTPException(
            status_code=403,
            detail=(
                f"Unauthorized: client IP {client_ip} is not associated with this device. "
                "Ensure the requesting IP is assigned to the device in the DCIM."
            ),
        )


@router.get("/{device_uuid}/boot-script", response_class=PlainTextResponse)
async def load_bootscript(device_uuid: str, request: Request) -> PlainTextResponse:
    """Load the bootscript for the given DCIM device ID."""
    return await load_configuration(device_uuid, "boot-script", request)


@router.get("/{device_uuid}/config/{configlet}", response_class=PlainTextResponse)
async def load_configuration(
    device_uuid: str, configlet: str, request: Request
) -> PlainTextResponse:
    """Load the specified configuration file for the given DCIM device ID."""
    await _authorize_request(request, device_uuid)
    try:
        device_data = await _get_device_data(device_uuid)
        content = await device_data.load_file(configlet)
        return PlainTextResponse(content)
    except (DCIMNotFoundError, ConfigStoreFileNotFound) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConfigStoreException as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ONIE process pulls image first, then looks for $url.ztp to load the ZTP script
# where $url is whats supplied by DHCP in boot-file-name
# Add some onie shortcuts so that image and boot script can be selected from the
# same DHCP boot-file-name option
@router.get("/{device_uuid}/onie", response_class=PlainTextResponse)
@router.get("/{device_uuid}/firmware", response_class=StreamingResponse)
async def load_firmware(device_uuid: str, request: Request) -> StreamingResponse:
    """Load the firmware for the given device."""
    await _authorize_request(request, device_uuid)
    try:
        device_data = await _get_device_data(device_uuid)
        if device_data.platform is None or device_data.version is None:
            raise HTTPException(status_code=404, detail="Device firware data not found")
    except DCIMNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    storage_client = get_storage_client()
    try:
        return await create_object_storage_streaming_response(
            storage_client,
            storage_client.get_firmware_object,
            device_data.platform,
            device_data.version,
            request=request,
        )
    except ObjectStorageNotFoundException as exc:
        raise HTTPException(status_code=404, detail="Firmware image not found in S3.") from exc


@router.get("/{device_uuid}/firmware/checksum")
async def load_firmware_checksum(device_uuid: str, request: Request) -> ChecksumResponse:
    """Load the firmware checksum for the given device."""
    await _authorize_request(request, device_uuid)
    try:
        device_data = await _get_device_data(device_uuid)
        if device_data.platform is None or device_data.version is None:
            raise HTTPException(status_code=404, detail="Device firware data not found")
    except DCIMNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    storage_client = get_storage_client()
    try:
        async with storage_client:
            checksum = await storage_client.get_firmware_checksum(
                device_data.platform, device_data.version
            )
        return ChecksumResponse(checksum=checksum)
    except ObjectStorageNotFoundException as exc:
        raise HTTPException(status_code=404, detail="Firmware image not found in S3.") from exc


@router.post("/{device_uuid}/provisioned")
async def mark_provisioned(device_uuid: str, request: Request) -> str:
    """Mark the ZTP process complete for the given device."""
    await _authorize_request(request, device_uuid)
    async with dcim_client_session() as client:
        await client.mark_ztp_device_provisioned(device_uuid)
    # Trigger a backup workflow
    try:
        workflow_client = temporal_client()
        async with workflow_client:
            await workflow_client.invoke_backup_workflow(device_uuid)
    except Exception as e:
        logger.error("Error invoking backup workflow: %s", e)
    return "OK"


class ValidateSerialBody(BaseModel):
    """Request body for serial number validation."""

    serial: str


def _compare_serials(expected: str, observed: str) -> bool:
    # Some devices may report part number and serial in the CID field
    # in the DHCP request in which case we'll have to update nautobot
    # with the extended serial data for DHCP to work
    # We've also seen some devices that may be reporting the serial
    # as the part number + serial within the OS but not in the CID field
    # so we'll allow comparisons both directions to avoid conflicts
    if observed.lower().endswith(expected.lower()):
        return True
    if expected.lower().endswith(observed.lower()):
        return True
    return False


@router.post("/{device_uuid}/validate_serial")
async def validate_serial(device_uuid: str, body: ValidateSerialBody, request: Request) -> str:
    """Validate the device serial number matches the selected DCIM."""
    await _authorize_request(request, device_uuid)
    try:
        async with dcim_client_session() as client:
            expected_serial = await client.get_device_serial(device_uuid)
        if not _compare_serials(expected_serial, body.serial):
            logger.error(
                "Serial number mismatch observed on device %s, expected: %s, observed: %s.",
                device_uuid,
                expected_serial,
                body.serial,
            )
            raise HTTPException(
                status_code=400,
                detail="Serial number does not match device in the DCIM.",
            )
        return "OK"
    except DCIMNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
