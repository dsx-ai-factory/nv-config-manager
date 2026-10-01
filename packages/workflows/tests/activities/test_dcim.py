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
"""Tests for the reusable DCIM activity lifecycle boundary."""

from __future__ import annotations

import re
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nv_config_manager_dcim.api import DCIMClient
from nv_config_manager_dcim.errors import DCIMError
from nv_config_manager_dcim.models import (
    DeviceVRF,
    HostInterfaceMetadata,
    HostMetadata,
    NamespaceRouteDistinguisher,
    SpectrumXVRF,
)
from nv_config_manager_dcim.workflow_models import (
    HostDeviceData,
    InterfaceData,
    NetworkDeviceData,
    Platform,
)
from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.activities.dcim import (
    AssignVrfToDeviceInput,
    AssignVrfToInterfaceInput,
    CheckRecordedConfigDriftInput,
    DeleteOverlayInput,
    GetAvailableRouteDistinguishersInput,
    GetDeviceInterfacesInput,
    GetDeviceVrfsInput,
    GetHostDeviceInput,
    GetHostDevicesInput,
    GetNetworkDeviceInput,
    GetNetworkDevicesInput,
    ProvisionVrfInput,
    QueryVRFByVPCInput,
    ReconcileSpXOverlayAssignmentsInput,
    RemoveUnmappedDeviceVrfsInput,
    SwitchPortByMacActivityInput,
    Vrf,
    VrfDeletionActivityInput,
    assign_vrf_to_device,
    assign_vrf_to_interface,
    check_recorded_config_drift,
    dcim_client_session,
    delete_overlay,
    delete_vrf,
    get_available_route_distinguishers,
    get_device_interfaces,
    get_device_vrfs,
    get_host_data_by_macs,
    get_host_data_by_names,
    get_host_device,
    get_host_devices,
    get_network_device,
    get_network_devices,
    get_switch_port_by_remote_mac_address,
    get_vrfs_by_overlay_id,
    provision_vrf,
    reconcile_spx_overlay_assignments,
    remove_unmapped_device_vrfs,
)
from nv_config_manager_workflows.activities.dcim.activities import _vni_from_rd
from nv_config_manager_workflows.runtime import (
    DCIMNotConfiguredError,
    configure_dcim_client,
)


class StubDCIMClient:
    """Minimal async context manager with observable lifecycle behavior."""

    def __init__(
        self,
        *,
        enter_error: BaseException | None = None,
        exit_error: BaseException | None = None,
    ) -> None:
        self.enter_error = enter_error
        self.exit_error = exit_error
        self.entered = False
        self.exited = False
        self.exit_exception: BaseException | None = None

    async def __aenter__(self) -> StubDCIMClient:
        self.entered = True
        if self.enter_error is not None:
            raise self.enter_error
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        self.exited = True
        self.exit_exception = exc_value
        if self.exit_error is not None:
            raise self.exit_error


class PermanentDCIMError(DCIMError):
    """Provider failure that Temporal must not retry."""

    non_retryable = True


def _configure_client(client: StubDCIMClient) -> None:
    configure_dcim_client(lambda: cast(DCIMClient, client))


def _activity_client() -> MagicMock:
    """Install an async-context-manager client whose methods are test controlled."""
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    configure_dcim_client(lambda: cast(DCIMClient, client))
    return client


def _network_device(name: str = "leaf-1") -> NetworkDeviceData:
    return NetworkDeviceData(
        id=f"{name}-id",
        name=name,
        role="leaf",
        site="site-1",
        device_type="switch",
        platform=Platform.CUMULUS_LINUX,
        primary_ip4="192.0.2.1",
        primary_ip6=None,
    )


def _host_device(name: str = "host-1") -> HostDeviceData:
    return HostDeviceData(
        id=f"{name}-id",
        name=name,
        role="server",
        site="site-1",
        device_type="server",
        serial="serial-1",
        device_bays=[],
        interfaces=[],
    )


async def test_session_enters_yields_and_exits_the_runtime_client() -> None:
    """Successful operations use the provider client's complete async lifecycle."""
    client = StubDCIMClient()
    _configure_client(client)

    async with dcim_client_session() as yielded:
        assert yielded is client
        assert client.entered is True
        assert client.exited is False

    assert client.exited is True
    assert client.exit_exception is None


@pytest.mark.parametrize(
    ("provider_error", "expected_non_retryable"),
    [
        (DCIMError("provider unavailable"), False),
        (PermanentDCIMError("invalid provider data"), True),
    ],
)
async def test_session_translates_provider_operation_errors(
    provider_error: DCIMError,
    expected_non_retryable: bool,
) -> None:
    """Provider errors preserve their message and retryability at the Temporal boundary."""
    client = StubDCIMClient()
    _configure_client(client)

    with pytest.raises(ApplicationError) as exc_info:
        async with dcim_client_session():
            raise provider_error

    assert exc_info.value.message == str(provider_error)
    assert exc_info.value.non_retryable is expected_non_retryable
    assert exc_info.value.__cause__ is provider_error
    assert client.exited is True
    assert client.exit_exception is provider_error


@pytest.mark.parametrize("failure_point", ["enter", "exit"])
async def test_session_translates_provider_lifecycle_errors(failure_point: str) -> None:
    """Client entry and cleanup share the same Temporal error contract."""
    provider_error = PermanentDCIMError(f"{failure_point} failed")
    client = StubDCIMClient(
        enter_error=provider_error if failure_point == "enter" else None,
        exit_error=provider_error if failure_point == "exit" else None,
    )
    _configure_client(client)

    with pytest.raises(ApplicationError) as exc_info:
        async with dcim_client_session():
            pass

    assert exc_info.value.message == f"{failure_point} failed"
    assert exc_info.value.non_retryable is True
    assert exc_info.value.__cause__ is provider_error


async def test_session_preserves_client_factory_errors() -> None:
    """Match the service boundary by leaving failures before client creation unchanged."""
    provider_error = PermanentDCIMError("factory failed")

    def client_provider() -> DCIMClient:
        raise provider_error

    configure_dcim_client(client_provider)

    with pytest.raises(PermanentDCIMError) as exc_info:
        async with dcim_client_session():
            pass

    assert exc_info.value is provider_error


async def test_session_does_not_translate_non_provider_errors() -> None:
    """Programming failures remain their original exception while still closing the client."""
    client = StubDCIMClient()
    failure = ValueError("bad activity input")
    _configure_client(client)

    with pytest.raises(ValueError) as exc_info:
        async with dcim_client_session():
            raise failure

    assert exc_info.value is failure
    assert client.exited is True
    assert client.exit_exception is failure


async def test_session_preserves_named_unconfigured_runtime_error(
    unconfigured_workflow_runtime: None,
) -> None:
    """Missing startup wiring remains a concrete non-retryable runtime failure."""
    with pytest.raises(DCIMNotConfiguredError, match="configure_dcim_client") as exc_info:
        async with dcim_client_session():
            pass

    assert exc_info.value.non_retryable is True


async def test_single_device_queries_preserve_inputs_and_outputs() -> None:
    """Single-device lookups pass identifiers through and wrap provider models."""
    client = _activity_client()
    network_device = _network_device()
    host_device = _host_device()
    client.get_network_device = AsyncMock(return_value=network_device)
    client.get_host_device = AsyncMock(return_value=host_device)

    network_output = await get_network_device(GetNetworkDeviceInput(device_id="network-id"))
    host_output = await get_host_device(GetHostDeviceInput(device_id="host-id"))

    assert network_output.device is network_device
    assert host_output.device is host_device
    client.get_network_device.assert_awaited_once_with("network-id")
    client.get_host_device.assert_awaited_once_with("host-id")
    assert client.__aenter__.await_count == 2
    assert client.__aexit__.await_count == 2


async def test_inventory_queries_preserve_provider_neutral_filters() -> None:
    """List activities map every legacy input field to the DCIM filter contract."""
    client = _activity_client()
    network_device = _network_device()
    host_device = _host_device()
    client.get_network_devices = AsyncMock(return_value=[network_device])
    client.get_host_devices = AsyncMock(return_value=[host_device])

    network_output = await get_network_devices(
        GetNetworkDevicesInput(
            site="site-1",
            roles=["leaf"],
            status=["active"],
            tenant="tenant-1",
            device_type_ids=["type-1"],
            mac_addresses=["00:11:22:33:44:55"],
            device_ids=["device-1"],
            render_enabled=True,
            deploy_enabled=False,
            backup_enabled=True,
            ztp_enabled=False,
            managed_only=True,
            platforms=[Platform.CUMULUS_LINUX],
        )
    )
    host_output = await get_host_devices(
        GetHostDevicesInput(
            site="site-1",
            roles=["server"],
            status=["active"],
            tenant="tenant-1",
            device_type_ids=["type-2"],
            mac_addresses=["66:77:88:99:AA:BB"],
        )
    )

    assert network_output.devices == [network_device]
    assert host_output.devices == [host_device]
    network_call = client.get_network_devices.await_args
    assert network_call is not None
    network_filter = network_call.args[0]
    assert network_filter.model_dump() == {
        "site": "site-1",
        "roles": ["leaf"],
        "statuses": ["active"],
        "tenant": "tenant-1",
        "device_type_ids": ["type-1"],
        "mac_addresses": ["00:11:22:33:44:55"],
        "device_ids": ["device-1"],
        "platforms": [Platform.CUMULUS_LINUX],
        "managed_only": True,
        "render_enabled": True,
        "deploy_enabled": False,
        "backup_enabled": True,
        "ztp_enabled": False,
    }
    host_call = client.get_host_devices.await_args
    assert host_call is not None
    host_filter = host_call.args[0]
    assert host_filter.model_dump() == {
        "site": "site-1",
        "roles": ["server"],
        "statuses": ["active"],
        "tenant": "tenant-1",
        "device_type_ids": ["type-2"],
        "mac_addresses": ["66:77:88:99:AA:BB"],
        "device_ids": None,
        "platforms": None,
        "managed_only": None,
        "render_enabled": None,
        "deploy_enabled": None,
        "backup_enabled": None,
        "ztp_enabled": None,
    }


async def test_host_metadata_queries_preserve_mac_format_alias_and_urls() -> None:
    """Host correlation output retains formatting and by-name empty interfaces."""
    client = _activity_client()
    host = HostMetadata(
        device_id="host-1",
        name="compute-1",
        tenant="tenant-1",
        alias="compute-alias",
        interfaces=(HostInterfaceMetadata(name="eth0", mac_address="001122334455"),),
    )
    client.get_host_metadata_by_macs = AsyncMock(return_value=[host])
    client.get_host_metadata_by_names = AsyncMock(return_value=[host])
    client.get_device_ui_url.side_effect = lambda device_id: f"https://dcim/{device_id}"

    by_mac = await get_host_data_by_macs(["00:11:22:33:44:55"])
    by_name = await get_host_data_by_names(["compute-1"])

    assert by_mac[0].model_dump() == {
        "interfaces": [{"name": "eth0", "mac": "00-11-22-33-44-55"}],
        "name": "compute-1",
        "tenant": "tenant-1",
        "device_id": "host-1",
        "url": "https://dcim/host-1",
        "alias": "compute-alias",
    }
    assert by_name[0].interfaces == []
    assert by_name[0].model_dump(exclude={"interfaces"}) == by_mac[0].model_dump(
        exclude={"interfaces"}
    )


async def test_available_route_distinguisher_uses_lowest_gap() -> None:
    """Route distinguisher selection retains namespace order and gap behavior."""
    client = _activity_client()
    client.get_namespace_route_distinguishers = AsyncMock(
        return_value=[
            NamespaceRouteDistinguisher(
                namespace_id="namespace-1",
                route_distinguishers=("*:1", "*:3", "not-an-rd"),
            ),
            NamespaceRouteDistinguisher(
                namespace_id="namespace-2",
                route_distinguishers=("*:4",),
            ),
        ]
    )

    output = await get_available_route_distinguishers(
        GetAvailableRouteDistinguishersInput(
            site="site-1",
            namespace_tag="managed",
            rd_min=1,
            rd_max=4,
        )
    )

    assert output.route_distinguisher == "*:2"
    assert output.namespaces == ["namespace-1", "namespace-2"]
    client.get_namespace_route_distinguishers.assert_awaited_once_with("site-1", "managed")


@pytest.mark.parametrize(
    ("namespaces", "message"),
    [
        ([], "No namespaces for site site-1 and tag managed."),
        (
            [
                NamespaceRouteDistinguisher(
                    namespace_id="namespace-1", route_distinguishers=("*:1",)
                )
            ],
            "Namespaces ['namespace-1'] out of space for new RDs",
        ),
    ],
)
async def test_available_route_distinguisher_failures_are_frozen(
    namespaces: list[NamespaceRouteDistinguisher],
    message: str,
) -> None:
    client = _activity_client()
    client.get_namespace_route_distinguishers = AsyncMock(return_value=namespaces)

    with pytest.raises(ApplicationError) as exc_info:
        await get_available_route_distinguishers(
            GetAvailableRouteDistinguishersInput(
                site="site-1",
                namespace_tag="managed",
                rd_min=1,
                rd_max=1,
            )
        )

    assert exc_info.value.message == message


async def test_overlay_vrf_query_preserves_mapping_and_absent_result() -> None:
    """Spectrum-X VRFs retain field mapping, ordering, and None for no matches."""
    client = _activity_client()
    provider_vrf = SpectrumXVRF(
        vrf_id="vrf-1",
        name="tenant-vrf",
        namespace="namespace-1",
        site="site-1",
        route_distinguisher="*:1001",
        interfaces=("leaf-1:swp1", "leaf-2:swp2"),
    )
    client.get_spectrum_x_vrfs = AsyncMock(side_effect=[[provider_vrf], []])
    activity_input = QueryVRFByVPCInput(
        overlay_id="overlay-1",
        site="site-1",
        namespace_tag="managed",
        namespace="namespace-1",
    )

    result = await get_vrfs_by_overlay_id(activity_input)
    absent = await get_vrfs_by_overlay_id(activity_input)

    assert result is not None
    assert result == [
        Vrf(
            name="tenant-vrf",
            namespace="namespace-1",
            site="site-1",
            id="vrf-1",
            rd="*:1001",
            interfaces=["leaf-1:swp1", "leaf-2:swp2"],
        )
    ]
    assert result[0].interface_count == 2
    assert absent is None
    assert client.get_spectrum_x_vrfs.await_args_list[0].args == (
        "overlay-1",
        "site-1",
        "namespace-1",
    )


async def test_switch_port_and_config_drift_queries_preserve_results() -> None:
    """Switch-port correlation and drift checks pass through provider results."""
    client = _activity_client()
    device = _network_device()
    client.get_connected_switch_port_by_remote_mac = AsyncMock(return_value=(device, "swp1"))
    client.has_recorded_config_drift = AsyncMock(return_value=True)

    switch_port = await get_switch_port_by_remote_mac_address(
        SwitchPortByMacActivityInput(remote_mac_address="00:11:22:33:44:55")
    )
    drift = await check_recorded_config_drift(CheckRecordedConfigDriftInput(device_id="device-1"))

    assert switch_port.device is device
    assert switch_port.interface == "swp1"
    assert drift is True


async def test_device_vrf_query_preserves_error_conversion() -> None:
    """The one read activity with explicit DCIM conversion retains retryability."""
    client = _activity_client()
    provider_error = PermanentDCIMError("provider rejected query")
    client.get_device_vrfs = AsyncMock(side_effect=provider_error)

    with pytest.raises(ApplicationError) as exc_info:
        await get_device_vrfs(GetDeviceVrfsInput(device_id="device-1"))

    assert exc_info.value.message == "provider rejected query"
    assert exc_info.value.type is None
    assert exc_info.value.details == ()
    assert exc_info.value.non_retryable is True
    assert exc_info.value.__cause__ is provider_error


async def test_other_read_queries_do_not_translate_dcim_errors() -> None:
    """Queries without an existing conversion boundary keep raw provider failures."""
    client = _activity_client()
    provider_error = DCIMError("provider unavailable")
    client.get_network_device = AsyncMock(side_effect=provider_error)

    with pytest.raises(DCIMError) as exc_info:
        await get_network_device(GetNetworkDeviceInput(device_id="device-1"))

    assert exc_info.value is provider_error


async def test_device_interface_filtering_preserves_order_and_missing_error() -> None:
    """Interface filtering retains provider order and the exact missing-name message."""
    client = _activity_client()
    interfaces = [
        InterfaceData(
            name="swp2",
            id="interface-2",
            host="leaf-1",
            mac_address=None,
            vrf_id="vrf-1",
        ),
        InterfaceData(
            name="swp1",
            id="interface-1",
            host="leaf-1",
            mac_address="00:11:22:33:44:55",
            vrf_id=None,
        ),
    ]
    client.get_device_interfaces = AsyncMock(return_value=interfaces)

    output = await get_device_interfaces(
        GetDeviceInterfacesInput(
            device_id="device-1",
            interface_names=["swp1", "swp2"],
        )
    )

    assert output.interfaces == interfaces

    with pytest.raises(ApplicationError) as exc_info:
        await get_device_interfaces(
            GetDeviceInterfacesInput(
                device_id="device-1",
                interface_names=["swp3", "swp4"],
            )
        )

    assert exc_info.value.message == "Interfaces not found on device device-1: swp3, swp4"


async def test_device_vrf_query_returns_provider_models() -> None:
    """Successful VRF queries wrap the provider-neutral objects without replacement."""
    client = _activity_client()
    vrfs = [DeviceVRF(vrf_id="vrf-1", vrf_name="tenant-vrf")]
    client.get_device_vrfs = AsyncMock(return_value=vrfs)

    output = await get_device_vrfs(GetDeviceVrfsInput(device_id="device-1"))

    assert output.vrfs == vrfs


@pytest.mark.parametrize(
    ("route_distinguisher", "expected"),
    [
        ("*:60004", 60004),
        ("0:1", 1),
    ],
)
def test_vni_from_route_distinguisher_preserves_parsing(
    route_distinguisher: str,
    expected: int,
) -> None:
    assert _vni_from_rd(route_distinguisher) == expected


@pytest.mark.parametrize("route_distinguisher", ["60004", "*:abc", "1:2:3"])
def test_vni_from_route_distinguisher_preserves_failure(
    route_distinguisher: str,
) -> None:
    with pytest.raises(
        ValueError,
        match=re.escape(f"Invalid route distinguisher {route_distinguisher!r}, expected '*:<vni>'"),
    ):
        _vni_from_rd(route_distinguisher)


async def test_provision_vrf_preserves_provider_arguments() -> None:
    client = _activity_client()
    client.provision_spectrum_x_vrf = AsyncMock(return_value=None)

    await provision_vrf(
        ProvisionVrfInput(
            namespaces=["namespace-1", "namespace-2"],
            route_distinguisher="*:60004",
            overlay_id="overlay-1",
            site="site-1",
            tenant="tenant-1",
        )
    )

    client.provision_spectrum_x_vrf.assert_awaited_once_with(
        ["namespace-1", "namespace-2"],
        "*:60004",
        60004,
        "overlay-1",
        "site-1",
        "tenant-1",
    )


async def test_provision_vrf_preserves_error_conversion() -> None:
    client = _activity_client()
    provider_error = PermanentDCIMError("provision rejected")
    client.provision_spectrum_x_vrf = AsyncMock(side_effect=provider_error)

    with pytest.raises(ApplicationError) as exc_info:
        await provision_vrf(
            ProvisionVrfInput(
                namespaces=["namespace-1"],
                route_distinguisher="*:60004",
                overlay_id="overlay-1",
                site="site-1",
                tenant="tenant-1",
            )
        )

    assert exc_info.value.message == "provision rejected"
    assert exc_info.value.non_retryable is True
    assert exc_info.value.__cause__ is provider_error


async def test_delete_and_assignment_mutations_preserve_provider_arguments() -> None:
    client = _activity_client()
    client.delete_spectrum_x_vrf = AsyncMock(return_value=None)
    client.delete_spectrum_x_overlay_if_unused = AsyncMock(side_effect=[True, False])
    client.assign_vrf_to_device = AsyncMock(return_value=None)
    client.assign_vrf_to_interface = AsyncMock(return_value=None)

    await delete_vrf(VrfDeletionActivityInput(vrf_id="vrf-1", vnid=60004))
    deleted = await delete_overlay(DeleteOverlayInput(overlay_id="overlay-1", site="site-1"))
    retained = await delete_overlay(DeleteOverlayInput(overlay_id="overlay-2", site="site-1"))
    await assign_vrf_to_device(AssignVrfToDeviceInput(device_id="device-1", vrf_id="vrf-1"))
    await assign_vrf_to_interface(
        AssignVrfToInterfaceInput(interface_id="interface-1", vrf_id="vrf-1")
    )
    await assign_vrf_to_interface(
        AssignVrfToInterfaceInput(interface_id="interface-2", vrf_id=None)
    )

    client.delete_spectrum_x_vrf.assert_awaited_once_with("vrf-1", 60004)
    assert deleted.model_dump() == {"deleted": True, "overlay_name": "overlay-1"}
    assert retained.model_dump() == {"deleted": False, "overlay_name": "overlay-2"}
    client.assign_vrf_to_device.assert_awaited_once_with("device-1", "vrf-1")
    assert client.assign_vrf_to_interface.await_args_list[0].args == ("interface-1", "vrf-1")
    assert client.assign_vrf_to_interface.await_args_list[1].args == ("interface-2", None)


async def test_untranslated_mutation_error_remains_provider_error() -> None:
    client = _activity_client()
    provider_error = DCIMError("delete unavailable")
    client.delete_spectrum_x_vrf = AsyncMock(side_effect=provider_error)

    with pytest.raises(DCIMError) as exc_info:
        await delete_vrf(VrfDeletionActivityInput(vrf_id="vrf-1", vnid=60004))

    assert exc_info.value is provider_error


async def test_reconcile_assignments_preserves_arguments_counts_and_retry_signal() -> None:
    client = _activity_client()
    client.reconcile_spectrum_x_overlay_assignments = AsyncMock(side_effect=[(2, 1), (0, 0)])
    activity_input = ReconcileSpXOverlayAssignmentsInput(
        overlay_id="overlay-1",
        site="site-1",
        device_id="device-1",
        interface_ids=["interface-1"],
        device_interface_ids=["interface-1", "interface-2"],
    )

    changed = await reconcile_spx_overlay_assignments(activity_input)
    with patch(
        "nv_config_manager_workflows.activities.dcim.activities.activity.info"
    ) as activity_info:
        activity_info.return_value.attempt = 2
        retried = await reconcile_spx_overlay_assignments(activity_input)

    assert changed.model_dump() == {
        "created": 2,
        "removed": 1,
        "reconciliation_changed": True,
    }
    assert retried.model_dump() == {
        "created": 0,
        "removed": 0,
        "reconciliation_changed": True,
    }
    assert client.reconcile_spectrum_x_overlay_assignments.await_args_list[0].args == (
        "overlay-1",
        "site-1",
        "device-1",
        ["interface-1"],
        ["interface-1", "interface-2"],
    )


async def test_reconcile_assignments_preserves_error_conversion() -> None:
    client = _activity_client()
    provider_error = PermanentDCIMError("reconciliation rejected")
    client.reconcile_spectrum_x_overlay_assignments = AsyncMock(side_effect=provider_error)

    with pytest.raises(ApplicationError) as exc_info:
        await reconcile_spx_overlay_assignments(
            ReconcileSpXOverlayAssignmentsInput(
                overlay_id=None,
                site="site-1",
                device_id="device-1",
                interface_ids=["interface-1"],
                device_interface_ids=["interface-1"],
            )
        )

    assert exc_info.value.message == "reconciliation rejected"
    assert exc_info.value.non_retryable is True
    assert exc_info.value.__cause__ is provider_error


async def test_remove_unmapped_device_vrfs_preserves_noop_and_provider_order() -> None:
    def unexpected_provider() -> DCIMClient:
        raise AssertionError("empty input must not request a DCIM client")

    configure_dcim_client(unexpected_provider)
    empty = await remove_unmapped_device_vrfs(
        RemoveUnmappedDeviceVrfsInput(device_id="device-1", vrf_ids=[])
    )
    assert empty.removed_vrf_ids == []

    client = _activity_client()
    client.remove_unmapped_device_vrfs = AsyncMock(return_value=["vrf-2", "vrf-1"])
    output = await remove_unmapped_device_vrfs(
        RemoveUnmappedDeviceVrfsInput(
            device_id="device-1",
            vrf_ids=["vrf-1", "vrf-2"],
        )
    )

    assert output.removed_vrf_ids == ["vrf-2", "vrf-1"]
    client.remove_unmapped_device_vrfs.assert_awaited_once_with("device-1", ["vrf-1", "vrf-2"])


async def test_remove_unmapped_device_vrfs_preserves_error_conversion() -> None:
    client = _activity_client()
    provider_error = PermanentDCIMError("cleanup rejected")
    client.remove_unmapped_device_vrfs = AsyncMock(side_effect=provider_error)

    with pytest.raises(ApplicationError) as exc_info:
        await remove_unmapped_device_vrfs(
            RemoveUnmappedDeviceVrfsInput(device_id="device-1", vrf_ids=["vrf-1"])
        )

    assert exc_info.value.message == "cleanup rejected"
    assert exc_info.value.non_retryable is True
    assert exc_info.value.__cause__ is provider_error
