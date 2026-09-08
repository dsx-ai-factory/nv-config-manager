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
"""Validate explicit workflow references and build initial search attributes."""

import asyncio
from collections.abc import Iterable
from typing import Any, cast

from fastapi import HTTPException
from pydantic import BaseModel

from nv_config_manager.dcim import DCIMClient, DeviceMetadata, create_dcim_client
from nv_config_manager.temporal.common.search_attributes import (
    DEVICE_ID_SEARCH_ATTRIBUTE,
    DEVICE_NAME_SEARCH_ATTRIBUTE,
    DEVICE_PLATFORM_SEARCH_ATTRIBUTE,
    DEVICE_ROLE_SEARCH_ATTRIBUTE,
    SITE_SEARCH_ATTRIBUTE,
)
from nv_config_manager.temporal.common.workflow_references import (
    WorkflowReference,
    WorkflowReferenceKind,
)

DEVICE_REFERENCE_LOOKUP_CONCURRENCY = 20


def _reference_metadata(body: BaseModel) -> Iterable[tuple[WorkflowReference, Any]]:
    """Yield explicitly annotated references and their values."""
    for field_name, field in type(body).model_fields.items():
        reference = next(
            (metadata for metadata in field.metadata if isinstance(metadata, WorkflowReference)),
            None,
        )
        if reference is not None:
            yield reference, getattr(body, field_name)


def _slugify(value: Any) -> str:
    """Match the workflow device metadata slug format."""
    return str(value).lower().replace(" ", "-")


async def _resolve_devices(
    client: DCIMClient,
    references: list[tuple[WorkflowReference, Any]],
) -> dict[str, list[Any]]:
    """Validate device references with provider-owned indexed lookups."""
    referenced_ids: list[str] = []
    enriched_ids: list[str] = []
    for reference, value in references:
        values = value if reference.many else [value]
        for item in values:
            if item is None:
                continue
            try:
                validated_item = reference.validator(item)
            except ValueError as error:
                raise HTTPException(status_code=422, detail=str(error)) from error
            device_id = cast(str, validated_item)
            if not client.is_valid_device_id(device_id):
                raise HTTPException(
                    status_code=422,
                    detail=f"Invalid device identifier for configured DCIM provider: {device_id}",
                )
            referenced_ids.append(device_id)
            if reference.enrich_search_attributes:
                enriched_ids.append(device_id)

    unique_ids = list(dict.fromkeys(referenced_ids))
    if not unique_ids:
        return {}

    lookup_slots = asyncio.Semaphore(DEVICE_REFERENCE_LOOKUP_CONCURRENCY)

    async def get_device_metadata(device_id: str) -> DeviceMetadata | None:
        """Bound concurrent provider lookups for collection references."""
        async with lookup_slots:
            return await client.get_device_metadata(device_id)

    devices = await asyncio.gather(*(get_device_metadata(device_id) for device_id in unique_ids))
    devices_by_id = {device.device_id: device for device in devices if device is not None}
    missing_ids = [device_id for device_id in unique_ids if device_id not in devices_by_id]
    if missing_ids:
        raise HTTPException(status_code=422, detail=f"Unknown device: {missing_ids[0]}")

    unique_enriched_ids = list(dict.fromkeys(enriched_ids))
    if len(unique_enriched_ids) > 1:
        raise HTTPException(status_code=422, detail="Conflicting device references")
    if len(unique_enriched_ids) != 1:
        return {}

    device_id = unique_enriched_ids[0]
    device = devices_by_id[device_id]
    attributes: dict[str, list[Any]] = {
        DEVICE_ID_SEARCH_ATTRIBUTE: [device_id],
        DEVICE_NAME_SEARCH_ATTRIBUTE: [device.name],
    }
    if device.role:
        attributes[DEVICE_ROLE_SEARCH_ATTRIBUTE] = [_slugify(device.role)]
    if device.platform:
        attributes[DEVICE_PLATFORM_SEARCH_ATTRIBUTE] = [_slugify(device.platform)]
    if device.site:
        attributes[SITE_SEARCH_ATTRIBUTE] = [device.site]
    return attributes


async def _resolve_location(client: DCIMClient, value: str) -> str:
    """Validate one provider-owned location ID and return its canonical name."""
    if not client.is_valid_location_id(value):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid location identifier for configured DCIM provider: {value}",
        )
    location = await client.get_location_metadata(value)
    if location is None:
        raise HTTPException(status_code=422, detail=f"Unknown location: {value}")
    return location.name


async def resolve_workflow_references(body: BaseModel) -> dict[str, list[Any]]:
    """Validate annotated references and return canonical initial search attributes."""
    references = list(_reference_metadata(body))
    device_references = [
        item for item in references if item[0].kind == WorkflowReferenceKind.DEVICE
    ]
    location_references = [
        item for item in references if item[0].kind == WorkflowReferenceKind.LOCATION
    ]
    if not device_references and not location_references:
        return {}

    client = create_dcim_client()
    async with client:
        attributes = await _resolve_devices(client, device_references)
        for reference, value in location_references:
            if reference.many:
                raise RuntimeError("Location reference collections are not supported")
            if value is not None:
                try:
                    validated_value = reference.validator(value)
                except ValueError as error:
                    raise HTTPException(status_code=422, detail=str(error)) from error
                # Explicit workflow scope takes precedence over device-derived metadata.
                attributes[SITE_SEARCH_ATTRIBUTE] = [
                    await _resolve_location(client, validated_value)
                ]
    return attributes
