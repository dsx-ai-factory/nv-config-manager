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
"""Input and output models for InfiniBand GUID discovery activities."""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel

from nv_config_manager_workflows.stage.models import StageOutput

IB_GUID_CF_KEY = "ib_guid"


class IBGuidMapping(BaseModel):
    """One UFM port -> DCIM interface mapping with the desired GUID action."""

    ufm_system_name: str
    ufm_port: str
    ufm_guid: str
    peer_switch_name: str
    peer_switch_port: str

    device_name: str = ""
    interface_name: str = ""
    interface_id: str = ""
    current_guid: str = ""

    action: str
    reason: str = ""

    @property
    def discovered_guid(self) -> str:
        """Alias retained for workflow readability."""
        return self.ufm_guid

    @staticmethod
    def _classify_action(current_guid: str, discovered_guid: str) -> str:
        """Decide the sync action for a known current/desired GUID pair."""
        current = (current_guid or "").strip().lower()
        desired = (discovered_guid or "").strip().lower()
        if not desired:
            return "skip"
        if current == desired:
            return "noop"
        if not current:
            return "set"
        return "update"

    @classmethod
    def from_skip_no_cable(
        cls,
        *,
        ufm_guid: str,
        system_name: str,
        ufm_port: str,
        peer_switch: str,
        peer_port: str,
    ) -> Self:
        """UFM port has no modeled cable from the peer switch port."""
        return cls(
            ufm_system_name=system_name,
            ufm_port=ufm_port,
            ufm_guid=ufm_guid,
            peer_switch_name=peer_switch,
            peer_switch_port=peer_port,
            action="skip",
            reason=(
                f"No cable modeled on {peer_switch} port {peer_port}; "
                "cannot resolve compute interface."
            ),
        )

    @classmethod
    def from_skip_iface_missing(
        cls,
        *,
        ufm_guid: str,
        system_name: str,
        ufm_port: str,
        peer_switch: str,
        peer_port: str,
        device_name: str,
        interface_name: str,
    ) -> Self:
        """Cable topology points at a device/interface not present in the DCIM."""
        return cls(
            ufm_system_name=system_name,
            ufm_port=ufm_port,
            ufm_guid=ufm_guid,
            peer_switch_name=peer_switch,
            peer_switch_port=peer_port,
            device_name=device_name,
            interface_name=interface_name,
            action="skip",
            reason=f"DCIM interface {device_name}/{interface_name} not found.",
        )

    @classmethod
    def from_resolved_iface(
        cls,
        *,
        ufm_guid: str,
        system_name: str,
        ufm_port: str,
        peer_switch: str,
        peer_port: str,
        device_name: str,
        interface_name: str,
        interface_id: str,
        current_guid: str,
    ) -> Self:
        """Mapping with a resolved DCIM interface and classified sync action."""
        return cls(
            ufm_system_name=system_name,
            ufm_port=ufm_port,
            ufm_guid=ufm_guid,
            peer_switch_name=peer_switch,
            peer_switch_port=peer_port,
            device_name=device_name,
            interface_name=interface_name,
            interface_id=interface_id,
            current_guid=current_guid,
            action=cls._classify_action(current_guid, ufm_guid),
        )


class DiscoverIBPortGuidsInput(BaseModel):
    """Input for the discovery activity."""

    ufm_host: str
    site: str | None = None
    switch_device_ids: list[str]


class DiscoverIBPortGuidsOutput(StageOutput):
    """Result of discovery."""

    mappings: list[IBGuidMapping]


class SyncIBGuidInput(BaseModel):
    """Input for syncing a single interface's `ib_guid` custom field."""

    interface_id: str
    guid: str
    dry_run: bool = True


class SyncIBGuidOutput(BaseModel):
    """Result of a sync attempt."""

    interface_id: str
    device_name: str = ""
    interface_name: str = ""
    previous_guid: str
    new_guid: str
    changed: bool
    dry_run: bool
    reason: str = ""


__all__ = [
    "IB_GUID_CF_KEY",
    "DiscoverIBPortGuidsInput",
    "DiscoverIBPortGuidsOutput",
    "IBGuidMapping",
    "SyncIBGuidInput",
    "SyncIBGuidOutput",
]
