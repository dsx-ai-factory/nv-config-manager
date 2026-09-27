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
"""Freeze DCIM, device, UFM, and InfiniBand activity extraction contracts."""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any, Union, cast, get_args, get_origin, get_type_hints

from pydantic import BaseModel
from temporalio import activity

from nv_config_manager.temporal.common.activities import REGISTERED_COMMON_ACTIVITIES
from nv_config_manager.temporal.hello_world.activities import (
    REGISTERED_ACTIVITIES as HELLO_WORLD_ACTIVITIES,
)
from nv_config_manager.temporal.ngc.activities import REGISTERED_ACTIVITIES as NGC_ACTIVITIES
from nv_config_manager.temporal.ngc.activities import dcim as dcim_activities
from nv_config_manager.temporal.ngc.activities import device as device_activities
from nv_config_manager.temporal.ngc.activities import ib_guid_discovery as ib_guid_activities
from nv_config_manager.temporal.ngc.activities import ib_pkey as ib_pkey_activities
from nv_config_manager.temporal.ngc.activities import ufm as ufm_activities
from nv_config_manager_workflows.registration.contract import activity_name

_REGISTERED_TYPES_FIXTURE = Path(__file__).parents[1] / "fixtures" / "registered_type_names.json"
_WORKFLOW_ROOT = Path(__file__).parents[3] / "nv_config_manager" / "temporal" / "ngc" / "workflows"

_ACTIVITY_EXPORTS = {
    dcim_activities: (
        "get_network_device",
        "get_host_device",
        "get_network_devices",
        "get_host_devices",
        "get_host_data_by_macs",
        "get_host_data_by_names",
        "get_available_route_distinguishers",
        "provision_vrf",
        "get_vrfs_by_overlay_id",
        "delete_vrf",
        "delete_overlay",
        "get_switch_port_by_remote_mac_address",
        "check_recorded_config_drift",
        "get_device_vrfs",
        "assign_vrf_to_device",
        "get_device_interfaces",
        "assign_vrf_to_interface",
        "reconcile_spx_overlay_assignments",
        "remove_unmapped_device_vrfs",
    ),
    device_activities: (
        "get_device_intended_neighbors",
        "get_device_actual_neighbors",
        "get_device_mac_table",
        "get_device_arp_table",
        "validate_hostname",
        "load_neighbor_data_by_switch_port",
    ),
    ufm_activities: ("get_ib_ports",),
    ib_pkey_activities: (
        "validate_pkey_available",
        "create_pkey_on_ufm",
        "verify_pkey_created",
        "add_guids_to_pkey",
        "fetch_pkey_members",
        "remove_guids_from_pkey",
        "set_pkey_members",
        "verify_pkey_members",
        "verify_pkey_members_absent",
    ),
    ib_guid_activities: (
        "discover_ib_port_guids",
        "sync_ib_guid_on_interface",
    ),
}

_EXPECTED_ACTIVITY_CONTRACTS = {
    "get_network_device": (
        True,
        "activity_input",
        "GetNetworkDeviceInput",
        "GetNetworkDeviceOutput",
    ),
    "get_host_device": (True, "activity_input", "GetHostDeviceInput", "GetHostDeviceOutput"),
    "get_network_devices": (
        True,
        "activity_input",
        "GetNetworkDevicesInput",
        "GetNetworkDevicesOutput",
    ),
    "get_host_devices": (
        True,
        "activity_input",
        "GetHostDevicesInput",
        "GetHostDevicesOutput",
    ),
    "get_host_data_by_macs": (True, "mac_addresses", "list[str]", "list[HostData]"),
    "get_host_data_by_names": (True, "device_names", "list[str]", "list[HostData]"),
    "get_available_route_distinguishers": (
        True,
        "activity_input",
        "GetAvailableRouteDistinguishersInput",
        "GetAvailableRouteDistinguishersOutput",
    ),
    "provision_vrf": (True, "activity_input", "ProvisionVrfInput", "None"),
    "get_vrfs_by_overlay_id": (
        True,
        "activity_input",
        "QueryVRFByVPCInput",
        "list[Vrf] | None",
    ),
    "delete_vrf": (True, "activity_input", "VrfDeletionActivityInput", "None"),
    "delete_overlay": (True, "activity_input", "DeleteOverlayInput", "DeleteOverlayOutput"),
    "get_switch_port_by_remote_mac_address": (
        True,
        "activity_input",
        "SwitchPortByMacActivityInput",
        "SwitchPortByMacActivityOutput",
    ),
    "check_recorded_config_drift": (
        True,
        "activity_input",
        "CheckRecordedConfigDriftInput",
        "bool",
    ),
    "get_device_vrfs": (
        True,
        "activity_input",
        "GetDeviceVrfsInput",
        "GetDeviceVrfsOutput",
    ),
    "assign_vrf_to_device": (True, "activity_input", "AssignVrfToDeviceInput", "None"),
    "get_device_interfaces": (
        True,
        "activity_input",
        "GetDeviceInterfacesInput",
        "GetDeviceInterfacesOutput",
    ),
    "assign_vrf_to_interface": (True, "activity_input", "AssignVrfToInterfaceInput", "None"),
    "reconcile_spx_overlay_assignments": (
        True,
        "activity_input",
        "ReconcileSpXOverlayAssignmentsInput",
        "ReconcileSpXOverlayAssignmentsOutput",
    ),
    "remove_unmapped_device_vrfs": (
        True,
        "activity_input",
        "RemoveUnmappedDeviceVrfsInput",
        "RemoveUnmappedDeviceVrfsOutput",
    ),
    "get_device_intended_neighbors": (
        True,
        "activity_input",
        "NetworkDeviceData",
        "DeviceNeighborData",
    ),
    "get_device_actual_neighbors": (
        False,
        "device_data",
        "NetworkDeviceData",
        "DeviceNeighborData",
    ),
    "get_device_mac_table": (False, "device_data", "NetworkDeviceData", "DeviceMacTable"),
    "get_device_arp_table": (False, "device_data", "NetworkDeviceData", "DeviceArpTable"),
    "validate_hostname": (
        False,
        "device_data",
        "NetworkDeviceData",
        "ValidateHostnameActivityOutput",
    ),
    "load_neighbor_data_by_switch_port": (
        False,
        "activity_input",
        "SwitchPortNeighborActivityInput",
        "InterfaceNeighborData | None",
    ),
    "get_ib_ports": (True, "input", "GetUFMPortsInput", "GetUFMPortsOutput"),
    "validate_pkey_available": (True, "input", "ValidatePKeyInput", "ValidatePKeyOutput"),
    "create_pkey_on_ufm": (True, "input", "CreatePKeyInput", "CreatePKeyOutput"),
    "verify_pkey_created": (True, "input", "VerifyPKeyInput", "VerifyPKeyOutput"),
    "add_guids_to_pkey": (True, "input", "AddGuidsInput", "AddGuidsOutput"),
    "fetch_pkey_members": (True, "input", "FetchPKeyMembersInput", "FetchPKeyMembersOutput"),
    "remove_guids_from_pkey": (True, "input", "RemoveGuidsInput", "RemoveGuidsOutput"),
    "set_pkey_members": (True, "input", "SetGuidsInput", "SetGuidsOutput"),
    "verify_pkey_members": (
        True,
        "input",
        "VerifyPKeyMembersInput",
        "VerifyPKeyMembersOutput",
    ),
    "verify_pkey_members_absent": (
        True,
        "input",
        "VerifyPKeyMembersAbsentInput",
        "VerifyPKeyMembersAbsentOutput",
    ),
    "discover_ib_port_guids": (
        True,
        "input",
        "DiscoverIBPortGuidsInput",
        "DiscoverIBPortGuidsOutput",
    ),
    "sync_ib_guid_on_interface": (True, "input", "SyncIBGuidInput", "SyncIBGuidOutput"),
}

_MODEL_EXPORTS = {
    dcim_activities: (
        "GetNetworkDeviceInput",
        "GetNetworkDeviceOutput",
        "GetHostDeviceInput",
        "GetHostDeviceOutput",
        "GetNetworkDevicesInput",
        "GetNetworkDevicesOutput",
        "GetHostDevicesInput",
        "GetHostDevicesOutput",
        "HostInterface",
        "HostData",
        "GetAvailableRouteDistinguishersInput",
        "GetAvailableRouteDistinguishersOutput",
        "Vrf",
        "ProvisionVrfInput",
        "QueryVRFByVPCInput",
        "VrfDeletionActivityInput",
        "DeleteOverlayInput",
        "DeleteOverlayOutput",
        "SwitchPortByMacActivityInput",
        "SwitchPortByMacActivityOutput",
        "CheckRecordedConfigDriftInput",
        "GetDeviceVrfsInput",
        "GetDeviceVrfsOutput",
        "AssignVrfToDeviceInput",
        "GetDeviceInterfacesInput",
        "GetDeviceInterfacesOutput",
        "AssignVrfToInterfaceInput",
        "ReconcileSpXOverlayAssignmentsInput",
        "ReconcileSpXOverlayAssignmentsOutput",
        "RemoveUnmappedDeviceVrfsInput",
        "RemoveUnmappedDeviceVrfsOutput",
    ),
    device_activities: (
        "ValidateHostnameActivityOutput",
        "SwitchPortNeighborActivityInput",
    ),
    ufm_activities: ("GetUFMPortsInput", "GetUFMPortsOutput"),
    ib_pkey_activities: (
        "ValidatePKeyInput",
        "ValidatePKeyOutput",
        "CreatePKeyInput",
        "CreatePKeyOutput",
        "VerifyPKeyInput",
        "VerifyPKeyOutput",
        "AddGuidsInput",
        "AddGuidsOutput",
        "FetchPKeyMembersInput",
        "FetchPKeyMembersOutput",
        "RemoveGuidsInput",
        "RemoveGuidsOutput",
        "SetGuidsInput",
        "SetGuidsOutput",
        "VerifyPKeyMembersInput",
        "VerifyPKeyMembersOutput",
        "VerifyPKeyMembersAbsentInput",
        "VerifyPKeyMembersAbsentOutput",
    ),
    ib_guid_activities: (
        "IBGuidMapping",
        "DiscoverIBPortGuidsInput",
        "DiscoverIBPortGuidsOutput",
        "SyncIBGuidInput",
        "SyncIBGuidOutput",
    ),
}

_EXPECTED_MODEL_SCHEMA_HASHES = {
    "GetNetworkDeviceInput": "c667624b242ad5d035d30fe52968238931144f61b1159560b54e73bdbb1769ac",
    "GetNetworkDeviceOutput": "0294053fb7ceeced54c19069acffeb28e68490069d9aa34cdef375e29d68dae3",
    "GetHostDeviceInput": "6d14977ed791a8e1f26e2e30b4a507d037d9b4300a19381181c8a11cb235a979",
    "GetHostDeviceOutput": "9478b56b08341a3dbaee0432e8d2189a930b0247d6a9a58ba20c4bdff021774b",
    "GetNetworkDevicesInput": "3bac198984fafd5cb4c6951cdaca0c91f2a2f9b50004a27b613f443e38bb856c",
    "GetNetworkDevicesOutput": "df76e9f4db49a938ac646cec3072398910226e4ae7e5c4d0fcad8ccab4bc6526",
    "GetHostDevicesInput": "ee2f19519f6272105d163473febf5a7782745cd8e2e41d128ce0cbfb9aecf342",
    "GetHostDevicesOutput": "a2819ac67d22369a4f08691f4ad216342a9ed32959a9316dd4d086f257584eeb",
    "HostInterface": "b2ae3abc792d579901473ddcb3048cac8225bfcaf5f5216f359001384763bda7",
    "HostData": "528e0b4fd84a9206884ab0bb7ed87e3e4e7b81077a295721ee1325b47ae052f5",
    "GetAvailableRouteDistinguishersInput": "65297a9b2da96eb4760d2917c3e9727424fcf2424950354b1fe24dc38f67fc00",
    "GetAvailableRouteDistinguishersOutput": "fa9020a734cdbc5223d18b5e093e291cea17d8cfa6dbf5aa7530a60d2419b35a",
    "Vrf": "3c36523e27c73cf3c5b69a6e5a2633097c0f2852c8f3b15dafd1a934fce4fc4d",
    "ProvisionVrfInput": "3d3fe5af24785734cddd5d992c604825aa44efdf756c17f1bf1535c52896a6ef",
    "QueryVRFByVPCInput": "9c895f92b7bf40091e4ff1f4e0613749cb957dc4603ed49098ef3762f34c6218",
    "VrfDeletionActivityInput": "47a10addf3552249c794687dbcdb3162e0e25be1bd25f8874695eb400f376609",
    "DeleteOverlayInput": "fe5383de73e525e8755164135082757cd1cf829c115d450bb7c6905658098c24",
    "DeleteOverlayOutput": "f10acc3643665a3e47ffb1d0a569ddcb4bdde18d1262cf88a805a6902db1142a",
    "SwitchPortByMacActivityInput": "281cba75b34429ed92c4d263869b829a013ac7d7ce6d1492e785da7f98e48590",
    "SwitchPortByMacActivityOutput": "bc0c23487c1e71d8bbe222891a678940b7db3524da7af0db6d294068c2e32ae8",
    "CheckRecordedConfigDriftInput": "13014368b52e7c4b01b0c3b18889a70441113d5afb16718e36bebacfe111d72c",
    "GetDeviceVrfsInput": "3d2545708f9b53ac0d87710795b9d12d6ee3d711d4fd226cf9dcc1580d07c719",
    "GetDeviceVrfsOutput": "3345b9a6522739fe6c6ecab50d1997f4c4f0710a62297942c4931db24f0dacdf",
    "AssignVrfToDeviceInput": "86da377082eab095a9e606d6aa434516615e57d4f895f47964a378893797b881",
    "GetDeviceInterfacesInput": "54447465715ccff869510e6256db290dc85f308ed6f8c5f5a380e93a84630fe9",
    "GetDeviceInterfacesOutput": "8a552fca1778fdd8087fe557d562bfc9cddb8c0d4717cd5274016c721dc70751",
    "AssignVrfToInterfaceInput": "0fc7cfa508de6312839766ffe21d155c17ec86300b98250c160d9164afd54f1e",
    "ReconcileSpXOverlayAssignmentsInput": "654229711001e4b72af827ad82e3abfcbe2a62f102d2942b99d44ef160fde95a",
    "ReconcileSpXOverlayAssignmentsOutput": "1bea9dd4c4e717d384f8c5fb8d9735f95e2367c23efafe010a924bbd1e9791f1",
    "RemoveUnmappedDeviceVrfsInput": "7b298433114a399664074bc5a2db0125bd367299285457fd15e9a32226d6719d",
    "RemoveUnmappedDeviceVrfsOutput": "ecfc9e8d52740e44747fccf0ded7c2affac8f92cbf5b06a891af0b6f5173beed",
    "ValidateHostnameActivityOutput": "1dd225106f522a944118725a3b723643a7320a25d4b52a07c0c0c007d9faf7b8",
    "SwitchPortNeighborActivityInput": "3f33ac8247c9376f6c52482dc7721ec178b16b1bf81bf3c3a1d20a4a28f1b7a4",
    "GetUFMPortsInput": "7c849b56f98a5ab3853254e984602fd418c8fce417012066749507e2f2a03617",
    "GetUFMPortsOutput": "10d16f1d36f18a11c6fdce27baa4f3840e20287de6fc3c8e577045adfbab5f5b",
    "ValidatePKeyInput": "d1d7f00cf2f7576bdd7be9f024882b69939cb5a1719d6c8531de2031c0b81e04",
    "ValidatePKeyOutput": "7a6931ed75a444a218f20241e8c6d3c27d5510f503bcc8ff92acade255d992c3",
    "CreatePKeyInput": "934df1ad71450e1d06bcefe88ef7d92ae55b3c78e4ec3304364c26687be67bd1",
    "CreatePKeyOutput": "54050910a4158829470eb601d3f9ef5a1d8c8e59ff706fdd257082f4aac95f03",
    "VerifyPKeyInput": "4dbbec33119b5515cdb7962e4bab8d7322972aa1f18bc4d6eade8ae00c1ce330",
    "VerifyPKeyOutput": "88eb86ae809ec4cd18994d49f477aa7b2a40f5b35d9421622c3fc174badd52a4",
    "AddGuidsInput": "cc76238c82b0d00de9dfcaba702a0ef32eae12cbf5adde6c95c4542a5478c38d",
    "AddGuidsOutput": "3575c61535dfe882e4a006720ab780a817cc568cdb3f1d5f725eff70c17c4438",
    "FetchPKeyMembersInput": "92c6c0f4b9fef069733fc9d0df813ef3c1ee7bb06ce16976b886a8e310a8580c",
    "FetchPKeyMembersOutput": "7178a28a9cc311234f3b2ccee789744955481eb036f0467411c699b98bf2049b",
    "RemoveGuidsInput": "3831c5166d5ccfe86e3b97f8a873b721cf3b57ee9ff7f42a840722df749c46f7",
    "RemoveGuidsOutput": "3c02238783f4dc9cd0a560cbe68e4a2c600cba18eadaadcaffa29e66593e2cac",
    "SetGuidsInput": "49f7eaa4b9c64a9f96342df6f70da23ef5d2684da7a1b104d62ced9669886eee",
    "SetGuidsOutput": "d0631a3eea0b19a8e01db968174a7e301ca600b6a4291f067f9ae484c8d65369",
    "VerifyPKeyMembersInput": "78204319c855cfebbb516e3e8a030f3fb8409d3d995245baa66f587fe81f4556",
    "VerifyPKeyMembersOutput": "148c820c03efd8d07efe82905e174b1b0f1cc461eecca8464da37579724ad0d3",
    "VerifyPKeyMembersAbsentInput": "4c31e8b5ddd62118d106e48a622f7c4b593ca45755ef606b48d0008844517373",
    "VerifyPKeyMembersAbsentOutput": "185a29031f5f4e8af740a3ba793aef7ac4b705570c5fe01e2f5d6ea5103cd232",
    "IBGuidMapping": "382c7b8333e003e330467ffbb541ca7ec8b5ef68760fc6baefd40f2fa074663f",
    "DiscoverIBPortGuidsInput": "2a85a97cc92f5764a582d404796f8dcfc65454651c6c1e11955f5191033594ef",
    "DiscoverIBPortGuidsOutput": "d24bce0d3903d51ab052c5eb5fcd3429445518dbf6b276f19e1409560f6c99e6",
    "SyncIBGuidInput": "e12a6ed4bd63d0621540fd6a976e0a44a996fdc7ae21d80cee2ca72ed404c015",
    "SyncIBGuidOutput": "7d7c4f993c9f91ebfa81c92ca45dac2cfc5c94fb61ad86d7f0e46fdb2a55c2ba",
}

_EXPECTED_WORKFLOW_CALL_COUNT = 77
_EXPECTED_WORKFLOW_CALL_HASH = "cca1f1440837ecd41b10b38b8981b285ab4617fd796e3722a062c0281ca7a90e"
_ACTIVITIES_WITHOUT_WORKFLOW_CALLS = {"get_host_device", "get_host_devices"}


def _type_name(annotation: Any) -> str:
    """Return a module-independent representation of a contract annotation."""
    if annotation is None or annotation is type(None):
        return "None"
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        return " | ".join(_type_name(argument) for argument in get_args(annotation))
    if origin is list:
        return f"list[{_type_name(get_args(annotation)[0])}]"
    return getattr(annotation, "__name__", str(annotation))


def _callable_contract(callable_: Any) -> tuple[bool, str, str, str]:
    """Describe a single-input activity independently of its implementation module."""
    signature = inspect.signature(callable_)
    parameters = tuple(signature.parameters.values())
    assert len(parameters) == 1
    parameter = parameters[0]
    assert parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert parameter.default is inspect.Parameter.empty
    type_hints = get_type_hints(callable_)
    return (
        inspect.iscoroutinefunction(callable_),
        parameter.name,
        _type_name(type_hints[parameter.name]),
        _type_name(type_hints["return"]),
    )


def _schema_hash(model: type[BaseModel]) -> str:
    """Fingerprint the complete JSON schema while ignoring dictionary insertion order."""
    serialized = json.dumps(model.model_json_schema(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def _workflow_call_contracts() -> list[tuple[str, str, str]]:
    """Capture workflow arguments and execution options as normalized AST."""
    contracts: list[tuple[str, str, str]] = []
    activity_names = set(_EXPECTED_ACTIVITY_CONTRACTS)
    for path in sorted(_WORKFLOW_ROOT.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            method = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else node.func.id
                if isinstance(node.func, ast.Name)
                else None
            )
            first_argument = node.args[0]
            called_activity = (
                first_argument.id
                if isinstance(first_argument, ast.Name)
                else first_argument.attr
                if isinstance(first_argument, ast.Attribute)
                else None
            )
            if method in {"execute_activity", "execute_local_activity"} and (
                called_activity in activity_names
            ):
                assert called_activity is not None
                contracts.append(
                    (
                        path.name,
                        called_activity,
                        ast.dump(node, include_attributes=False),
                    )
                )
    return contracts


def test_activity_names_signatures_and_execution_modes_are_frozen() -> None:
    """All 37 activity callables retain the contracts used by Temporal workflows."""
    actual = {}
    for module, exported_names in _ACTIVITY_EXPORTS.items():
        for exported_name in exported_names:
            callable_ = getattr(module, exported_name)
            temporal_name = activity._Definition.must_from_callable(callable_).name
            assert temporal_name == exported_name
            actual[temporal_name] = _callable_contract(callable_)

    assert actual == _EXPECTED_ACTIVITY_CONTRACTS
    assert len(actual) == 37


def test_activity_model_schemas_are_frozen() -> None:
    """All 58 activity-owned Pydantic schemas survive relocation unchanged."""
    actual = {
        model_name: _schema_hash(getattr(module, model_name))
        for module, model_names in _MODEL_EXPORTS.items()
        for model_name in model_names
    }

    assert actual == _EXPECTED_MODEL_SCHEMA_HASHES


def test_representative_model_payloads_and_defaults_are_frozen() -> None:
    """Representative serialized inputs preserve optional and deprecated defaults."""
    assert dcim_activities.GetNetworkDevicesInput().model_dump() == {
        "site": None,
        "roles": None,
        "status": None,
        "tenant": None,
        "device_type_ids": None,
        "mac_addresses": None,
        "device_ids": None,
        "render_enabled": None,
        "deploy_enabled": None,
        "backup_enabled": None,
        "ztp_enabled": None,
        "managed_only": None,
        "platforms": None,
    }
    assert dcim_activities.GetDeviceInterfacesInput(device_id="device-1").model_dump() == {
        "device_id": "device-1",
        "interface_names": None,
    }
    assert device_activities.ValidateHostnameActivityOutput(hostname="leaf-1").model_dump() == {
        "hostname": "leaf-1"
    }
    assert ufm_activities.GetUFMPortsInput(host="ufm.example.com").model_dump() == {
        "host": "ufm.example.com",
        "unhealthy": False,
        "site": None,
    }
    assert ib_pkey_activities.ValidatePKeyInput(host="ufm.example.com").model_dump() == {
        "host": "ufm.example.com",
        "site": None,
        "pkey": None,
        "pkey_min": 1,
        "pkey_max": 32766,
    }
    assert ib_pkey_activities.CreatePKeyInput(
        host="ufm.example.com", pkey="0x0001"
    ).model_dump() == {
        "host": "ufm.example.com",
        "site": None,
        "pkey": "0x0001",
        "ip_over_ib": True,
        "index0": None,
    }
    assert ib_pkey_activities.VerifyPKeyMembersInput(
        host="ufm.example.com",
        pkey="0x0001",
        expected_guids=["guid-1"],
    ).model_dump() == {
        "host": "ufm.example.com",
        "site": None,
        "pkey": "0x0001",
        "expected_guids": ["guid-1"],
        "expected_memberships": None,
        "exact": False,
    }
    assert ib_guid_activities.SyncIBGuidInput(
        interface_id="interface-1", guid="guid-1"
    ).model_dump() == {
        "interface_id": "interface-1",
        "guid": "guid-1",
        "dry_run": True,
    }


def test_constants_and_aliases_are_frozen() -> None:
    """Public constants and the compatibility model alias stay stable."""
    assert dcim_activities.DeviceVrfInfo is dcim_activities.DeviceVRF
    assert ib_pkey_activities.PKEY_MIN == 0x0001
    assert ib_pkey_activities.PKEY_MAX == 0x7FFE
    assert ib_pkey_activities.PKEY_RESERVED == {0x7FFF}
    assert ib_guid_activities.IB_GUID_CF_KEY == "ib_guid"


def test_private_helpers_are_not_exposed_through_legacy_facades() -> None:
    """Implementation helpers stay on package-owned modules, not compatibility paths."""
    assert not hasattr(dcim_activities, "_vni_from_rd")
    assert not hasattr(ufm_activities, "_generate_ports_csv")


def test_registered_type_name_snapshot_contains_extracted_activities_exactly_once() -> None:
    """Start from the reconciled 121-name worker registration baseline."""
    expected = json.loads(_REGISTERED_TYPES_FIXTURE.read_text())["activities"]
    registered = [
        activity_name(cast(Callable[..., Any], callable_))
        for callable_ in (
            *NGC_ACTIVITIES,
            *HELLO_WORLD_ACTIVITIES,
            *REGISTERED_COMMON_ACTIVITIES,
        )
    ]
    assert all(name is not None for name in registered)
    actual = [name for name in registered if name is not None]

    assert sorted(actual) == expected
    assert len(actual) == len(set(actual)) == 121
    assert set(_EXPECTED_ACTIVITY_CONTRACTS) <= set(actual)
    assert all(actual.count(name) == 1 for name in _EXPECTED_ACTIVITY_CONTRACTS)


def test_workflow_activity_arguments_and_options_are_frozen() -> None:
    """Import moves must not alter workflow calls or Temporal options."""
    contracts = _workflow_call_contracts()
    serialized = json.dumps(contracts, separators=(",", ":"))
    called_names = {activity_name_ for _, activity_name_, _ in contracts}

    assert len(contracts) == _EXPECTED_WORKFLOW_CALL_COUNT
    assert hashlib.sha256(serialized.encode()).hexdigest() == _EXPECTED_WORKFLOW_CALL_HASH
    assert set(_EXPECTED_ACTIVITY_CONTRACTS) - called_names == _ACTIVITIES_WITHOUT_WORKFLOW_CALLS


def test_dcim_device_infiniband_workflows_import_extracted_activities() -> None:
    """Production workflows no longer depend on service compatibility facades."""
    extracted_service_modules = {
        "nv_config_manager.temporal.ngc.activities.dcim",
        "nv_config_manager.temporal.ngc.activities.device",
        "nv_config_manager.temporal.ngc.activities.ib_guid_discovery",
        "nv_config_manager.temporal.ngc.activities.ib_pkey",
        "nv_config_manager.temporal.ngc.activities.ufm",
    }
    violations: list[str] = []

    for path in sorted(_WORKFLOW_ROOT.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in extracted_service_modules:
                violations.append(f"{path.name}:{node.lineno}: {node.module}")

    assert violations == []
