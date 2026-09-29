# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""In-memory runtime providers for InfiniBand PKey workflow tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Self, cast

from nv_config_manager_dcim import (
    DCIMClient,
    IBHostSite,
    IBInterfaceGuid,
    IBPKeyAssignment,
    IBPKeyCleanup,
    IBPKeyContext,
    IBPKeyPartition,
)

from nv_config_manager_workflows.activities.ib_pkey import IB_PKEY_ACTIVITIES
from nv_config_manager_workflows.activities.lock import LOCK_ACTIVITIES
from nv_config_manager_workflows.activities.nats import NATS_ACTIVITIES
from nv_config_manager_workflows.clients.ufm import UFMClient, UFMClientError
from nv_config_manager_workflows.runtime import configure_dcim_client, configure_ufm_client

HOST = "ufm.example.com"
SITE_ID = "site-1"
SITE_NAME = "test-site"
OVERLAY_ID = "overlay-444"
OVERLAY_NAME = "ib-pkey-overlay"
PKEY_ID = "pkey-1"
PKEY = "0x0005"
GUID_1 = "0002c903000e0b72"
GUID_2 = "0002c903000e0b73"
INTERFACE_ID_1 = "iface-001"
INTERFACE_ID_2 = "iface-002"

PKEY_WORKFLOW_ACTIVITIES = [*IB_PKEY_ACTIVITIES, *NATS_ACTIVITIES, *LOCK_ACTIVITIES]


@dataclass(slots=True)
class FakePKey:
    """Mutable UFM representation of one PKey partition."""

    members: dict[str, str] = field(default_factory=dict)
    ip_over_ib: bool = True


class FakeUFMClient:
    """Stateful package-local stand-in for the provider-neutral UFM client."""

    def __init__(self) -> None:
        self.pkeys: dict[str, FakePKey] = {}
        self.put_payloads: list[dict[str, Any]] = []
        self.calls: list[tuple[str, str]] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        return None

    def set_pkey(
        self,
        pkey: str = PKEY,
        *,
        members: dict[str, str] | None = None,
        ip_over_ib: bool = True,
    ) -> None:
        """Seed one partition."""
        self.pkeys[pkey] = FakePKey(
            members={
                guid.lower(): membership.lower() for guid, membership in (members or {}).items()
            },
            ip_over_ib=ip_over_ib,
        )

    async def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """Apply the small UFM REST surface used by canonical PKey activities."""
        self.calls.append((method, path))
        payload = kwargs.get("json") or {}

        if method == "GET" and path == "/resources/pkeys":
            return {pkey: {} for pkey in self.pkeys}

        if method == "POST" and path == "/resources/pkeys/add":
            self.set_pkey(
                str(payload["pkey"]),
                ip_over_ib=bool(payload.get("ip_over_ib", True)),
            )
            return {}

        if method == "PUT" and path == "/resources/pkeys/":
            pkey = str(payload["pkey"])
            guids = [str(guid).lower() for guid in payload.get("guids", [])]
            memberships = [str(value).lower() for value in payload.get("memberships", [])]
            self.pkeys[pkey] = FakePKey(
                members=dict(zip(guids, memberships, strict=True)),
                ip_over_ib=bool(payload.get("ip_over_ib", True)),
            )
            self.put_payloads.append(dict(payload))
            return {}

        if method == "DELETE" and "/guids/" in path:
            prefix, guids_csv = path.rsplit("/guids/", 1)
            pkey = prefix.rsplit("/", 1)[-1]
            partition = self.pkeys.get(pkey)
            if partition is not None:
                for guid in guids_csv.split(","):
                    partition.members.pop(guid.lower(), None)
            return {}

        if method == "GET" and path.startswith("/resources/pkeys/"):
            pkey = path.rsplit("/", 1)[-1]
            partition = self.pkeys.get(pkey)
            if partition is None:
                raise UFMClientError("not found", status_code=404)
            return {
                "pkey": pkey,
                "guids": [
                    {"guid": guid, "membership": membership}
                    for guid, membership in partition.members.items()
                ],
                "ip_over_ib": partition.ip_over_ib,
            }

        raise AssertionError(f"Unexpected fake UFM request: {method} {path}")


class FakeDCIMClient:
    """Stateful provider-neutral DCIM client for PKey workflow execution."""

    def __init__(self) -> None:
        self.host_site = IBHostSite(
            device_id="ufm-device-1",
            device_name=HOST,
            device_primary_ip=HOST,
            site_id=SITE_ID,
            site_name=SITE_NAME,
        )
        self.interfaces = {
            ("hca01", "mlx5_0"): IBInterfaceGuid(
                interface_id=INTERFACE_ID_1,
                device_name="hca01",
                interface_name="mlx5_0",
                guid=GUID_1,
            ),
            ("hca01", "mlx5_1"): IBInterfaceGuid(
                interface_id=INTERFACE_ID_2,
                device_name="hca01",
                interface_name="mlx5_1",
                guid=GUID_2,
            ),
        }
        self.assignments: dict[str, IBPKeyAssignment] = {}
        self.pkey_ids = {PKEY: PKEY_ID}
        self.resolve_site_calls = 0
        self._assignment_number = 0

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        return None

    def _new_assignment_id(self) -> str:
        self._assignment_number += 1
        return f"assignment-{self._assignment_number:03d}"

    def seed_assignment(
        self,
        interface_id: str,
        guid: str,
        membership_type: str = "full",
        *,
        assignment_id: str | None = None,
    ) -> str:
        """Seed one tracked overlay assignment."""
        identifier = assignment_id or self._new_assignment_id()
        self.assignments[interface_id] = IBPKeyAssignment(
            assignment_id=identifier,
            interface_id=interface_id,
            guid=guid,
            membership_type=membership_type,
        )
        return identifier

    async def canonicalize_ib_host(self, host: str) -> str:
        return HOST if host == self.host_site.device_name else host

    async def resolve_ib_host_site(self, host: str) -> IBHostSite:
        self.resolve_site_calls += 1
        return self.host_site

    async def resolve_ib_pkey_context(
        self,
        host: str,
        pkey: str,
        *,
        create_overlay_for_orphan: bool = False,
    ) -> IBPKeyContext:
        return IBPKeyContext(
            host_site=self.host_site,
            overlay_id=OVERLAY_ID,
            overlay_name=OVERLAY_NAME,
            pkey_id=self.pkey_ids.setdefault(pkey, PKEY_ID),
            pkey=pkey,
        )

    async def get_ib_interface_records(
        self, device_interface_pairs: list[tuple[str, str]]
    ) -> list[IBInterfaceGuid]:
        return [self.interfaces[pair] for pair in device_interface_pairs if pair in self.interfaces]

    async def find_ib_interfaces_by_guids(self, guids: list[str]) -> list[IBInterfaceGuid]:
        normalized = {guid.lower() for guid in guids}
        return [record for record in self.interfaces.values() if record.guid.lower() in normalized]

    async def ensure_ib_pkey_assignments(
        self, overlay_id: str, assignments: list[tuple[str, str, str]]
    ) -> list[str]:
        identifiers = []
        for interface_id, guid, membership in assignments:
            existing = self.assignments.get(interface_id)
            if existing is None:
                identifier = self.seed_assignment(interface_id, guid, membership)
            else:
                identifier = existing.assignment_id
                self.assignments[interface_id] = existing.model_copy(
                    update={"guid": guid, "membership_type": membership}
                )
            identifiers.append(identifier)
        return identifiers

    async def remove_ib_pkey_assignments(
        self, overlay_id: str, interface_ids: list[str]
    ) -> tuple[list[str], list[str]]:
        removed = []
        missing = []
        for interface_id in interface_ids:
            assignment = self.assignments.pop(interface_id, None)
            if assignment is None:
                missing.append(interface_id)
            else:
                removed.append(assignment.assignment_id)
        return removed, missing

    async def get_ib_pkey_assignments(self, overlay_id: str) -> list[IBPKeyAssignment]:
        return list(self.assignments.values())

    async def sync_ib_pkey_assignments(
        self, overlay_id: str, desired_assignments: list[tuple[str, str, str]]
    ) -> tuple[list[str], list[str], list[str]]:
        desired = {assignment[0]: assignment for assignment in desired_assignments}
        removed = [
            assignment.assignment_id
            for interface_id, assignment in list(self.assignments.items())
            if interface_id not in desired and self.assignments.pop(interface_id, None) is not None
        ]
        added = []
        unchanged = []
        for interface_id, guid, membership in desired_assignments:
            existing = self.assignments.get(interface_id)
            if existing is None:
                added.append(self.seed_assignment(interface_id, guid, membership))
            else:
                unchanged.append(existing.assignment_id)
                self.assignments[interface_id] = existing.model_copy(
                    update={"guid": guid, "membership_type": membership}
                )
        return added, removed, unchanged

    async def cleanup_ib_pkey_partition(
        self,
        overlay_id: str,
        overlay_name: str,
        pkey_id: str,
        pkey: str,
        ufm_partition_empty: bool,
    ) -> IBPKeyCleanup:
        remaining = len(self.assignments)
        partition_empty = remaining == 0 and ufm_partition_empty
        return IBPKeyCleanup(
            partition_empty=partition_empty,
            pkey_deleted=partition_empty,
            overlay_deleted=partition_empty and overlay_name.startswith("ib-pkey-"),
            remaining_assignments=remaining,
        )

    async def ensure_orphan_ib_pkey(self, pkey: str) -> IBPKeyPartition:
        return IBPKeyPartition(pkey_id=self.pkey_ids.setdefault(pkey, PKEY_ID), pkey=pkey)

    async def ensure_ib_pkey_partition(
        self,
        pkey: str,
        partition_name: str,
        location_name: object,
        tenant_name: str | None,
        membership_type: str,
    ) -> IBPKeyPartition:
        return IBPKeyPartition(
            pkey_id=self.pkey_ids.setdefault(pkey, PKEY_ID),
            pkey=pkey,
            partition_id=OVERLAY_ID,
            partition_name=partition_name,
        )


@dataclass(slots=True)
class PKeyRuntime:
    """Both provider fakes installed for one test."""

    dcim: FakeDCIMClient
    ufm: FakeUFMClient


def install_pkey_runtime() -> PKeyRuntime:
    """Install fresh provider fakes through the canonical runtime hooks."""
    runtime = PKeyRuntime(dcim=FakeDCIMClient(), ufm=FakeUFMClient())
    configure_dcim_client(lambda: cast(DCIMClient, runtime.dcim))
    configure_ufm_client(lambda _host, _site: cast(UFMClient, runtime.ufm))
    return runtime


__all__ = [
    "GUID_1",
    "GUID_2",
    "HOST",
    "INTERFACE_ID_1",
    "INTERFACE_ID_2",
    "OVERLAY_ID",
    "OVERLAY_NAME",
    "PKEY",
    "PKEY_ID",
    "PKEY_WORKFLOW_ACTIVITIES",
    "FakeDCIMClient",
    "FakePKey",
    "FakeUFMClient",
    "PKeyRuntime",
    "install_pkey_runtime",
]
