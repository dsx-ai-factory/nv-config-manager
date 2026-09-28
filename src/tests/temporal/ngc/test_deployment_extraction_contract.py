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
"""Freeze configuration-lifecycle activity extraction contracts."""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any, Union, cast, get_args, get_origin, get_type_hints

from nv_config_manager_dcim.workflow_models import NetworkDeviceData, Platform
from pydantic import BaseModel
from temporalio import activity

from nv_config_manager.temporal.common.activities import REGISTERED_COMMON_ACTIVITIES
from nv_config_manager.temporal.hello_world.activities import (
    REGISTERED_ACTIVITIES as HELLO_WORLD_ACTIVITIES,
)
from nv_config_manager.temporal.ngc.activities import REGISTERED_ACTIVITIES as NGC_ACTIVITIES
from nv_config_manager.temporal.ngc.activities import backup as backup_activities
from nv_config_manager.temporal.ngc.activities import deploy as deploy_activities
from nv_config_manager.temporal.ngc.activities import (
    device_password_rotation as password_activities,
)
from nv_config_manager.temporal.ngc.activities import (
    nvlinkswitch_firmware as nvlink_activities,
)
from nv_config_manager.temporal.ngc.activities import os as os_activities
from nv_config_manager.temporal.ngc.activities import render as render_activities
from nv_config_manager_workflows.registration.contract import activity_name

_REGISTERED_TYPES_FIXTURE = Path(__file__).parents[1] / "fixtures" / "registered_type_names.json"
_WORKFLOW_ROOT = Path(__file__).parents[3] / "nv_config_manager" / "temporal" / "ngc" / "workflows"

_ACTIVITY_EXPORTS = {
    backup_activities: (
        "load_running_configuration",
        "persist_config_backup",
        "record_backup_config_manager_plugin",
    ),
    deploy_activities: (
        "load_intended_configuration",
        "load_partial_configuration",
        "perform_candidate_diff",
        "apply_approved_configuration",
        "validate_config_diff",
        "wait_for_tenant_render",
    ),
    render_activities: ("execute_render",),
    os_activities: (
        "validate_rendered_image_change",
        "get_current_os",
        "get_os_image_versions",
        "update_intended_os_image",
        "execute_ztp",
        "poll_image",
        "poll_ztp_status",
        "wait_reboot",
        "get_mlnx_os_version",
        "download_mlnx_os",
        "install_mlnx_os",
        "reload_mlnx_os",
        "cleanup_mlnx_os",
    ),
    password_activities: ("validate_rendered_password_change",),
    nvlink_activities: (
        "get_running_firmware",
        "compare_running_desired",
        "update_device_context",
        "validate_render_targets",
        "validate_target_files",
        "reboot_device",
    ),
}

_EXPECTED_ACTIVITY_CONTRACTS = {
    "load_running_configuration": (False, "device_data", "NetworkDeviceData", "str"),
    "persist_config_backup": (True, "activity_input", "PersistConfigBackupInput", "str"),
    "record_backup_config_manager_plugin": (
        True,
        "activity_input",
        "RecordBackupConfigManagerPluginInput",
        "tuple[bool, str]",
    ),
    "load_intended_configuration": (
        True,
        "device_data",
        "NetworkDeviceData",
        "tuple[str, str, str]",
    ),
    "load_partial_configuration": (
        True,
        "activity_input",
        "LoadPartialConfigurationActivityInput",
        "tuple[str, str, str]",
    ),
    "perform_candidate_diff": (False, "activity_input", "DiffActivityInput", "str"),
    "apply_approved_configuration": (
        False,
        "activity_input",
        "ConfigApplyActivityInput",
        "None",
    ),
    "validate_config_diff": (
        False,
        "activity_input",
        "ValidateConfigDiffActivityInput",
        "ValidateConfigDiffActivityOutput",
    ),
    "wait_for_tenant_render": (
        True,
        "activity_input",
        "WaitForTenantRenderInput",
        "WaitForTenantRenderOutput",
    ),
    "execute_render": (True, "activity_input", "ExecuteRenderInput", "ExecuteRenderOutput"),
    "validate_rendered_image_change": (
        True,
        "activity_input",
        "ValidateRenderedImageChangeInput",
        "bool",
    ),
    "validate_rendered_password_change": (
        True,
        "activity_input",
        "ValidateRenderedPasswordChangeInput",
        "bool",
    ),
    "get_current_os": (False, "activity_input", "GetCurrentOSInput", "GetCurrentOSOutput"),
    "get_os_image_versions": (
        True,
        "activity_input",
        "GetOSImageVersionsInput",
        "GetOSImageVersionsOutput",
    ),
    "update_intended_os_image": (
        True,
        "activity_input",
        "UpdateIntendedOSImageInput",
        "None",
    ),
    "execute_ztp": (False, "activity_input", "ExecuteZTPInput", "ExecuteZTPOutput"),
    "poll_image": (False, "activity_input", "PollImageInput", "PollImageOutput"),
    "poll_ztp_status": (
        False,
        "activity_input",
        "PollZTPStatusInput",
        "PollZTPStatusOutput",
    ),
    "wait_reboot": (False, "activity_input", "WaitRebootInput", "WaitRebootOutput"),
    "get_mlnx_os_version": (
        False,
        "activity_input",
        "GetMlnxOSVersionInput",
        "GetMlnxOSVersionOutput",
    ),
    "download_mlnx_os": (
        False,
        "activity_input",
        "DownloadMlnxOSInput",
        "DownloadMlnxOSOutput",
    ),
    "install_mlnx_os": (
        False,
        "activity_input",
        "InstallMlnxOSInput",
        "InstallMlnxOSOutput",
    ),
    "reload_mlnx_os": (
        False,
        "activity_input",
        "ReloadMlnxOSInput",
        "ReloadMlnxOSOutput",
    ),
    "cleanup_mlnx_os": (
        False,
        "activity_input",
        "CleanupMlnxOSInput",
        "CleanupMlnxOSOutput",
    ),
    "get_running_firmware": (
        False,
        "activity_input",
        "GetRunningFirmwareInput",
        "GetRunningFirmwareOutput",
    ),
    "compare_running_desired": (
        True,
        "activity_input",
        "CompareRunningDesiredInput",
        "CompareRunningDesiredOutput",
    ),
    "update_device_context": (True, "activity_input", "UpdateDeviceContextInput", "None"),
    "validate_render_targets": (True, "activity_input", "ValidateRenderTargetsInput", "None"),
    "validate_target_files": (True, "activity_input", "ValidateTargetFilesInput", "None"),
    "reboot_device": (False, "activity_input", "RebootDeviceInput", "RebootDeviceOutput"),
}

_MODEL_EXPORTS = {
    backup_activities: (
        "PersistConfigBackupInput",
        "RecordBackupConfigManagerPluginInput",
    ),
    deploy_activities: (
        "LoadPartialConfigurationActivityInput",
        "DiffActivityInput",
        "ConfigApplyActivityInput",
        "ValidateConfigDiffActivityInput",
        "ValidateConfigDiffActivityOutput",
        "WaitForTenantRenderInput",
        "WaitForTenantRenderOutput",
    ),
    render_activities: ("ExecuteRenderInput", "ExecuteRenderOutput"),
    os_activities: (
        "ValidateRenderedImageChangeInput",
        "GetCurrentOSInput",
        "GetCurrentOSOutput",
        "GetOSImageVersionsInput",
        "GetOSImageVersionsOutput",
        "UpdateIntendedOSImageInput",
        "ExecuteZTPInput",
        "ExecuteZTPOutput",
        "PollImageInput",
        "PollImageOutput",
        "PollZTPStatusInput",
        "PollZTPStatusOutput",
        "WaitRebootInput",
        "WaitRebootOutput",
        "GetMlnxOSVersionInput",
        "GetMlnxOSVersionOutput",
        "DownloadMlnxOSInput",
        "DownloadMlnxOSOutput",
        "InstallMlnxOSInput",
        "InstallMlnxOSOutput",
        "ReloadMlnxOSInput",
        "ReloadMlnxOSOutput",
        "CleanupMlnxOSInput",
        "CleanupMlnxOSOutput",
    ),
    password_activities: ("ValidateRenderedPasswordChangeInput",),
    nvlink_activities: (
        "GetRunningFirmwareInput",
        "GetRunningFirmwareOutput",
        "CompareRunningDesiredInput",
        "CompareRunningDesiredOutput",
        "UpdateDeviceContextInput",
        "ValidateRenderTargetsInput",
        "ValidateTargetFilesInput",
        "RebootDeviceInput",
        "RebootDeviceOutput",
    ),
}

_EXPECTED_MODEL_SCHEMA_HASHES = {
    "PersistConfigBackupInput": "e48b7d92f16b7a824dcfbe028137aa441ada3ef65b58102fa187b1654d0b1423",
    "RecordBackupConfigManagerPluginInput": "7e311b45a2a5f5f8b96ada7ed413e164c0927ce6d8c648455353d69e541cb2fc",
    "LoadPartialConfigurationActivityInput": "1b9265df6129077251efa5140cda9b596923313a238d20ea835e697e7999e986",
    "DiffActivityInput": "307f9e29ba98c869c72190e3b761c05faa4a1e23e8aa91b5399b863debb2e769",
    "ConfigApplyActivityInput": "a5bb6d4d4524bf9ad5e78b5700b8b34c21424e53c1a87fcfedffbf9ff3bcefd4",
    "ValidateConfigDiffActivityInput": "9e972e8e56d822315da9bb8694c38ff03b99cb1ec413000ff3f5ca9c65419ad1",
    "ValidateConfigDiffActivityOutput": "06e9f1e22fa2d78bcac555b7a839a4ab2cd1ca307755fc44d5268fbaba28c5b2",
    "WaitForTenantRenderInput": "8cd1314db8f82dea2ddb6cce62f6337495011f84b98140b56956b7b799189479",
    "WaitForTenantRenderOutput": "8f50b4e24686b044480ed403aecf466b8289885a42a5173475b10f93b3df3c2e",
    "ExecuteRenderInput": "d0592a877b79e80b17dd3ea9dbd19fba9c8326a2396d6547303257471802352a",
    "ExecuteRenderOutput": "3fb5b08f821a0e7b21456d889bb0d2b5a6e68ddec2f2626c9de9d919b622eae3",
    "ValidateRenderedImageChangeInput": "691b672af7a8691bb0e5c9ed6ca8f3812a6bdf8926d166b836b4d9a08c5f18ac",
    "ValidateRenderedPasswordChangeInput": "5bf5a3ad1f37e257f1ddb610ebfc8838f8e971f9ec32150ac02812c9d181ac54",
    "GetCurrentOSInput": "a0287cfbdfd069a814aeecd5aced257e72b9ebbbdc5e83c1ef2da1e7b82b3503",
    "GetCurrentOSOutput": "56b58496aa333de359443a409c3c393f65b326cba1815818888adec1709a94f4",
    "GetOSImageVersionsInput": "422caf647439d4d25f6f13e4b56e433b5090a07d757c20a3cf304b4f2f0165df",
    "GetOSImageVersionsOutput": "5af3808516f9fa920ae6c3d7e9b98b73b55482d7d67ab5f560a9db8b5759afe4",
    "UpdateIntendedOSImageInput": "a91b1b51b45856c509b738b3792c403686846b64ff5ea2b9c9a5a12424ee43f6",
    "ExecuteZTPInput": "187f1fdd9f5ae6d47de4cc14f2daa1e00aa5500f3a3e3f5d0dd60303b374cc01",
    "ExecuteZTPOutput": "2cb310c8664e15389492654ddf96d81ae017ea21c754df39e6ceb234d6e0fa21",
    "PollImageInput": "f0e343edd52839c296c7b032c8af9474c6fde98fa3fdceb23fd3773672c31228",
    "PollImageOutput": "540720bc1e19fadf803f0a36032daed5c1a81f260ea4194786d2349d6aa0f824",
    "PollZTPStatusInput": "ab821067f481f49aaeea68bb60e4cf73039d44ed5e0d080e6cc5c5a00e0b6a5b",
    "PollZTPStatusOutput": "4c2098de569cd521ad5e67462598388b9c57b30c8be9aeba6ec5c3a5a369e2a1",
    "WaitRebootInput": "94ae27852dcb569e8a98f10e615860e57f2cd3b2ddab65c0a552b7a08a279d48",
    "WaitRebootOutput": "1dd876bf37ef860eec6d169096f16873c1c8a068a1f80ac12b833528597f016a",
    "GetMlnxOSVersionInput": "7df015b5148af2bd94a06a7b05c68ea4edfcc769c8711a278feea5b56db9b016",
    "GetMlnxOSVersionOutput": "56881836f2554498a59cfbad215570d2bfec8b0eb7c354cbe913a93d9cbf1ce1",
    "DownloadMlnxOSInput": "51daa8def2e56e9dc66f2b006027a5e97eaeb430b3111a9a350516ded4f8a466",
    "DownloadMlnxOSOutput": "3592b9e610e48ca26cf3391362b188ebef017808049be4eb08d38eb94c93fe36",
    "InstallMlnxOSInput": "4260538433364c1faad51d8c4073a77f55b4e685c8e256ce9d0ddd633c82db25",
    "InstallMlnxOSOutput": "b02259ace4993cb062dec3c2ad40cdb03baaaacef55ef5ce6bbd51dc73f3cd48",
    "ReloadMlnxOSInput": "8f66ee7785e1976ce76f744faa57311f4f8624378a857498405ef24965d988d0",
    "ReloadMlnxOSOutput": "6635d406c09c2926f2e3ea6bf96d7f97b60f07f0a3a025007138c0b81fac9106",
    "CleanupMlnxOSInput": "a866eaf7f6eca9380a826e079d2c6a634fe161ef30bc1dd1b3be4933e8db3053",
    "CleanupMlnxOSOutput": "6fdfd68f6aaea788b15a30db3211ec6383fcf49115292f0732b543080df8882d",
    "GetRunningFirmwareInput": "5b1e2bd264dff06d016ff8d053d1d4ade88e740e8c2201d41aa60801e1cf8493",
    "GetRunningFirmwareOutput": "d15c20b747e7e01da56d7bf797a93008c5b4440c7e4361d55ce172874791fa55",
    "CompareRunningDesiredInput": "9c31ae4cb1755fe07cfb16c7057acc96dfb7817979712393b9f82304b7d94e28",
    "CompareRunningDesiredOutput": "670001c4b2d9df83c9c379e36a20f52401b8443cdf7d2a35ce11745e1d565541",
    "UpdateDeviceContextInput": "17493daf3050795ea8a8b4ec4d924078303747f81987d79072b5cae38482964a",
    "ValidateRenderTargetsInput": "d85ce45c0d801c64dfbc4e54ae02dc4ac60238da1401604f3c2f955657c3e0d9",
    "ValidateTargetFilesInput": "c1dc668eee01898ee850d361df1d7b378ad53f4a59a035b937075e974e1b4393",
    "RebootDeviceInput": "e844040ada634ddb2947af0d552cbb0b5848fc71c44ea8c9dee0803d0e35dddc",
    "RebootDeviceOutput": "8e995f633d6e91cc6d2faacd08fbaacf02a7af9bf6d1c80bf2f0406fff45ce16",
}

_EXPECTED_WORKFLOW_CALL_COUNT = 57
_EXPECTED_WORKFLOW_CALL_HASH = "25d1ed1ce9481e3ffe7aef1de93bdd6f0e7aff3f389fe91c0791cc67bb3b1e88"
_ACTIVITIES_WITHOUT_WORKFLOW_CALLS = {
    "validate_rendered_password_change",
    "validate_target_files",
}


def _device_data() -> NetworkDeviceData:
    """Return representative provider-neutral device data for payload contracts."""
    return NetworkDeviceData(
        id="device-1",
        name="leaf-1",
        role="leaf",
        site="site-1",
        device_type="switch",
        platform=Platform.CUMULUS_LINUX,
        primary_ip4="192.0.2.1",
        primary_ip6=None,
    )


def _type_name(annotation: Any) -> str:
    """Return a module-independent representation of a contract annotation."""
    if annotation is None or annotation is type(None):
        return "None"
    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if origin in (Union, types.UnionType):
        return " | ".join(_type_name(argument) for argument in arguments)
    if origin is list:
        return f"list[{_type_name(arguments[0])}]"
    if origin is tuple:
        return f"tuple[{', '.join(_type_name(argument) for argument in arguments)}]"
    if origin is dict:
        return f"dict[{_type_name(arguments[0])}, {_type_name(arguments[1])}]"
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
    """Capture deployment workflow arguments and execution options as normalized AST."""
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
    """All 30 activity callables retain the contracts used by Temporal workflows."""
    actual = {}
    for module, exported_names in _ACTIVITY_EXPORTS.items():
        for exported_name in exported_names:
            callable_ = getattr(module, exported_name)
            temporal_name = activity._Definition.must_from_callable(callable_).name
            assert temporal_name == exported_name
            actual[temporal_name] = _callable_contract(callable_)

    assert actual == _EXPECTED_ACTIVITY_CONTRACTS
    assert len(actual) == 30


def test_activity_model_schemas_are_frozen() -> None:
    """All 45 activity-owned Pydantic schemas survive relocation unchanged."""
    actual = {
        model_name: _schema_hash(getattr(module, model_name))
        for module, model_names in _MODEL_EXPORTS.items()
        for model_name in model_names
    }

    assert actual == _EXPECTED_MODEL_SCHEMA_HASHES


def test_representative_model_payloads_and_defaults_are_frozen() -> None:
    """Representative payloads preserve optional fields and operational defaults."""
    device = _device_data()

    assert backup_activities.PersistConfigBackupInput(
        device_data=device,
        device_running_config="nv set system hostname leaf-1",
        commit_message="scheduled backup",
        user="operator",
        user_domain=None,
    ).model_dump(exclude={"device_data"}) == {
        "device_running_config": "nv set system hostname leaf-1",
        "commit_message": "scheduled backup",
        "user": "operator",
        "user_domain": None,
    }
    assert backup_activities.RecordBackupConfigManagerPluginInput(
        workflow_id="workflow-1",
        device_id="device-1",
        commit_id="7",
        path="device-1/startup.yaml",
        user="operator",
        commit_message="scheduled backup",
        deployed_commit_id=None,
    ).model_dump() == {
        "workflow_id": "workflow-1",
        "device_id": "device-1",
        "commit_id": "7",
        "path": "device-1/startup.yaml",
        "user": "operator",
        "commit_message": "scheduled backup",
        "deployed_commit_id": None,
    }
    assert deploy_activities.LoadPartialConfigurationActivityInput(
        device_data=device,
        config_file="tenant.yaml",
    ).model_dump(exclude={"device_data"}) == {
        "config_file": "tenant.yaml",
        "commit_id": None,
    }
    assert deploy_activities.DiffActivityInput(
        device_data=device,
        configuration="nv set interface swp1",
    ).model_dump(exclude={"device_data"}) == {
        "configuration": "nv set interface swp1",
        "partial": False,
    }
    assert deploy_activities.ConfigApplyActivityInput(
        device_data=device,
        configuration="nv set interface swp1",
        approved_diff="nv set interface swp1",
    ).model_dump(exclude={"device_data"}) == {
        "configuration": "nv set interface swp1",
        "approved_diff": "nv set interface swp1",
        "partial": False,
        "commit_confirm": True,
    }
    assert deploy_activities.ValidateConfigDiffActivityInput(
        tenant_config="tenant",
        diff="",
    ).model_dump() == {
        "tenant_config": "tenant",
        "diff": "",
        "allowed_patterns": None,
        "disallowed_patterns": None,
    }
    assert deploy_activities.ValidateConfigDiffActivityOutput(valid=True).model_dump() == {
        "valid": True,
        "message": None,
    }
    assert deploy_activities.WaitForTenantRenderInput(
        device=device,
        config_id="7",
    ).model_dump(exclude={"device"}) == {
        "config_id": "7",
        "interval": 10,
        "max_attempts": 60,
    }
    assert deploy_activities.WaitForTenantRenderOutput(config_id=None).model_dump() == {
        "config_id": None
    }
    assert render_activities.ExecuteRenderOutput().model_dump() == {
        "updated_files": [],
        "snapshot_files": [],
    }
    assert os_activities.PollImageOutput().model_dump() == {"running_image": None}
    assert os_activities.PollZTPStatusInput(device_data=device).model_dump(
        exclude={"device_data"}
    ) == {
        "timeout_minutes": 30,
        "ztp_execution_timestamp": None,
    }
    assert os_activities.WaitRebootInput(
        device_data=device,
        ztp_execution_timestamp="2026-01-02T03:04:05",
    ).model_dump(exclude={"device_data"}) == {
        "ztp_execution_timestamp": "2026-01-02T03:04:05",
        "timeout": 10,
    }
    assert nvlink_activities.CompareRunningDesiredOutput(
        upgrade_needed=True,
        desired_os="5.0.0",
        desired_firmware={"cpld": "1.2.3"},
        differences={"cpld": {"actual": "1.2.2", "expected": "1.2.3"}},
    ).model_dump() == {
        "upgrade_needed": True,
        "desired_os": "5.0.0",
        "desired_firmware": {"cpld": "1.2.3"},
        "differences": {"cpld": {"actual": "1.2.2", "expected": "1.2.3"}},
    }


def test_registered_type_name_snapshot_contains_deployment_activities_exactly_once() -> None:
    """Deployment extraction starts from the reconciled worker registration baseline."""
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
    """Import moves must not alter deployment workflow calls or Temporal options."""
    contracts = _workflow_call_contracts()
    serialized = json.dumps(contracts, separators=(",", ":"))
    called_names = {activity_name_ for _, activity_name_, _ in contracts}

    assert len(contracts) == _EXPECTED_WORKFLOW_CALL_COUNT
    assert hashlib.sha256(serialized.encode()).hexdigest() == _EXPECTED_WORKFLOW_CALL_HASH
    assert set(_EXPECTED_ACTIVITY_CONTRACTS) - called_names == _ACTIVITIES_WITHOUT_WORKFLOW_CALLS
