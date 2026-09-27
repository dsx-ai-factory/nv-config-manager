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
"""Freeze the service contracts for the planned IB/DCIM package extraction."""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager

import pytest
from nv_config_manager_dcim import IBHostSite
from pydantic import BaseModel
from temporalio import activity

from nv_config_manager.temporal.ngc.activities import ib_dcim, ib_nautobot
from nv_config_manager.temporal.ngc.workflows.ib_pkey_member_add import (
    IBPKeyMemberAddInput,
    IBPKeyMemberAddWorkflow,
)
from nv_config_manager.temporal.ngc.workflows.ib_pkey_member_delete import (
    IBPKeyMemberDeleteInput,
    IBPKeyMemberDeleteWorkflow,
)
from nv_config_manager.temporal.ngc.workflows.ib_pkey_member_update import (
    IBPKeyMemberUpdateInput,
    IBPKeyMemberUpdateWorkflow,
)
from nv_config_manager_workflows.activities.ib_dcim import IB_DCIM_ACTIVITIES
from nv_config_manager_workflows.metadata import WorkflowMetadataMixin, build_workflow_lock_key

_CANONICAL_HOST = "10.0.0.5"
_UFM_DEVICE_NAME = "ufm01"
_PKEY = "0x0100"
_GUID = "0002c903000e0b72"

_ACTIVITY_CONTRACTS = {
    "cleanup_empty_pkey_partition": (
        "CleanupEmptyPartitionInput",
        "CleanupEmptyPartitionOutput",
    ),
    "create_partition_in_dcim": (
        "CreatePartitionInDCIMInput",
        "CreatePartitionInDCIMOutput",
    ),
    "create_partition_in_nautobot": (
        "CreatePartitionInDCIMInput",
        "CreatePartitionInDCIMOutput",
    ),
    "fetch_pkey_assignments": (
        "FetchPKeyAssignmentsInput",
        "FetchPKeyAssignmentsOutput",
    ),
    "record_ib_pkey_in_dcim": (
        "RecordIBPKeyInDCIMInput",
        "RecordIBPKeyInDCIMOutput",
    ),
    "record_ib_pkey_in_nautobot": (
        "RecordIBPKeyInDCIMInput",
        "RecordIBPKeyInDCIMOutput",
    ),
    "record_pkey_assignments": (
        "RecordPKeyAssignmentsInput",
        "RecordPKeyAssignmentsOutput",
    ),
    "remove_pkey_assignments": (
        "RemovePKeyAssignmentsInput",
        "RemovePKeyAssignmentsOutput",
    ),
    "resolve_guids_to_interfaces": (
        "ResolveGuidsToInterfacesInput",
        "ResolveGuidsToInterfacesOutput",
    ),
    "resolve_ib_context": ("ResolveIBContextInput", "ResolveIBContextOutput"),
    "resolve_ib_context_for_add": ("ResolveIBContextInput", "ResolveIBContextOutput"),
    "resolve_ib_site_for_host": (
        "ResolveIBSiteForHostInput",
        "ResolveIBSiteForHostOutput",
    ),
    "resolve_interface_guids": (
        "ResolveInterfaceGuidsInput",
        "ResolveInterfaceGuidsOutput",
    ),
    "sync_pkey_assignments": (
        "SyncPKeyAssignmentsInput",
        "SyncPKeyAssignmentsOutput",
    ),
}

# These hashes describe the JSON payload schemas recorded in Temporal history.
# Update them only for an intentional, replay-reviewed contract change.
_PAYLOAD_SCHEMA_SHA256 = {
    "CleanupEmptyPartitionInput": "67e5a7050791029e2c20d91d8b58bd140efd1480325e05150c22953559e67522",
    "CleanupEmptyPartitionOutput": "7b49cd114b46dc106db42ced001ff6d27242a4bd0b0f53f75ad4877f8fd2d7a6",
    "CreatePartitionInDCIMInput": "1e80966bf03fcce4be17fe8fb1cf41d19261ed1cb26d6aab440aef67e99994c7",
    "CreatePartitionInDCIMOutput": "f1175a046ddc117add5964a9e88cee011485627980f7cf4a88926d7dfbadbf62",
    "FetchPKeyAssignmentsInput": "3798462c6345d47507f1fa9a5a8bf3308d9b903c25a4e4b7b331059f46300fbf",
    "FetchPKeyAssignmentsOutput": "3dc678298d1a742a1ab45eae44a5cfb17195d43d372aceafee3c7f11edefd3bd",
    "RecordIBPKeyInDCIMInput": "50e4aea98631d05553df29cc66645e639536d3369d896d1782151bbd64be86ff",
    "RecordIBPKeyInDCIMOutput": "deb19642ec9ec821f14440330655f996149c398a3366c481450c35114c3bc1f3",
    "RecordPKeyAssignmentsInput": "c395312c7d14c9d79eee80fad3a2aa3b786023ab7355ae7c49d6efb4ab33b683",
    "RecordPKeyAssignmentsOutput": "350ffa0fbbc9dd2dc9981da3a6eaf519476d2a25c90ebeeb9cd669a5d509adc3",
    "RemovePKeyAssignmentsInput": "4231d4ac9849e02b2d754b901981262be77070ac37042be9b9df7c0d09d8cd83",
    "RemovePKeyAssignmentsOutput": "87f77349941fcac6b4a4fb7674347e93b3127d344fbdf773f3e44823b84f4bad",
    "ResolveGuidsToInterfacesInput": "71e3d665a329728b2ba0643fe77ef399df05de3fd7b154eb4099f0a5ee303237",
    "ResolveGuidsToInterfacesOutput": "9618afe976cad2c28cd9cfe3038636705ee77c4aaec3fffb1a7f908deec08375",
    "ResolveIBContextInput": "444bf5c92b5d5e6f8512f5f18669c75a2756cdb47713b6bead3d07e9fd9e8ac0",
    "ResolveIBContextOutput": "f96fbdc25e8a796d2d281a07081ff4f58e9db1480e56a1240f14a25056ceeb2f",
    "ResolveIBSiteForHostInput": "795f770d38ba14c1ac049ce1aec2eaebb040b7daf063f939df2f18e3be0affad",
    "ResolveIBSiteForHostOutput": "9f7ed63cea1db4aa066ef2788ea7d7388c56afebbab101e0b993a2aa9e8641d9",
    "ResolveInterfaceGuidsInput": "70467d89fc45115ea8be67d7776f5ffa000ab78bd2e068213e6f5a95b5c69c8f",
    "ResolveInterfaceGuidsOutput": "d90746b107c946ef609d9855f9a5bf63f612e27f32906fc75c65c64c811aa4c9",
    "SyncPKeyAssignmentsInput": "85d0acaf3ce2c46e4b9c01cf1c41ab33d8c926bd89da667482f2cb36221fc6eb",
    "SyncPKeyAssignmentsOutput": "9bf7e2e0203715bb71a41cfd7eb281037597d3dc41fe2da8afe27a945241fab1",
}


def _schema_digest(model: type[BaseModel]) -> str:
    """Return a deterministic fingerprint for a Temporal payload schema."""
    encoded = json.dumps(
        model.model_json_schema(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def test_ib_dcim_activity_names_and_payload_types_are_frozen() -> None:
    """Moving implementations must not change names recorded in workflow history."""
    actual_contracts: dict[str, tuple[str, str]] = {}

    for attribute_name, (input_name, output_name) in _ACTIVITY_CONTRACTS.items():
        activity_callable = getattr(ib_dcim, attribute_name)
        assert inspect.iscoroutinefunction(activity_callable)
        definition = activity._Definition.must_from_callable(activity_callable)
        assert definition.name == attribute_name
        assert definition.name is not None
        assert definition.arg_types is not None
        assert len(definition.arg_types) == 1
        assert definition.ret_type is not None
        actual_contracts[definition.name] = (
            definition.arg_types[0].__name__,
            definition.ret_type.__name__,
        )
        assert actual_contracts[definition.name] == (input_name, output_name)

    assert actual_contracts == _ACTIVITY_CONTRACTS
    assert len(actual_contracts) == len(IB_DCIM_ACTIVITIES) == 14
    assert {
        activity._Definition.must_from_callable(item).name for item in IB_DCIM_ACTIVITIES
    } == set(_ACTIVITY_CONTRACTS)


@pytest.mark.parametrize(
    (
        "legacy_activity",
        "modern_activity",
        "legacy_input",
        "modern_input",
        "legacy_output",
        "modern_output",
    ),
    [
        (
            "create_partition_in_nautobot",
            "create_partition_in_dcim",
            "CreatePartitionInNautobotInput",
            "CreatePartitionInDCIMInput",
            "CreatePartitionInNautobotOutput",
            "CreatePartitionInDCIMOutput",
        ),
        (
            "record_ib_pkey_in_nautobot",
            "record_ib_pkey_in_dcim",
            "RecordIBPKeyInNautobotInput",
            "RecordIBPKeyInDCIMInput",
            "RecordIBPKeyInNautobotOutput",
            "RecordIBPKeyInDCIMOutput",
        ),
    ],
)
def test_legacy_activity_contracts_remain_explicit(
    legacy_activity: str,
    modern_activity: str,
    legacy_input: str,
    modern_input: str,
    legacy_output: str,
    modern_output: str,
) -> None:
    """Legacy names retain their own Temporal entries and modern payload types."""
    legacy_definition = activity._Definition.must_from_callable(getattr(ib_dcim, legacy_activity))
    modern_definition = activity._Definition.must_from_callable(getattr(ib_dcim, modern_activity))

    assert legacy_definition.name == legacy_activity
    assert modern_definition.name == modern_activity
    assert getattr(ib_dcim, legacy_input) is getattr(ib_dcim, modern_input)
    assert getattr(ib_dcim, legacy_output) is getattr(ib_dcim, modern_output)


def test_ib_dcim_temporal_payload_schemas_are_frozen() -> None:
    """The activity move must retain the JSON shapes already stored in histories."""
    actual = {
        model_name: _schema_digest(getattr(ib_dcim, model_name))
        for model_name in _PAYLOAD_SCHEMA_SHA256
    }

    assert actual == _PAYLOAD_SCHEMA_SHA256


def test_legacy_ib_nautobot_module_is_the_ib_dcim_module() -> None:
    """The oldest service import path must keep exposing the same objects."""
    assert ib_nautobot is ib_dcim
    for activity_name in _ACTIVITY_CONTRACTS:
        assert getattr(ib_nautobot, activity_name) is getattr(ib_dcim, activity_name)

    private_names = {
        name for name in vars(ib_dcim) if name.startswith("_") and not name.startswith("__")
    }
    assert private_names == {"_dcim_workflow_client"}


class _CanonicalizationClient:
    """Minimal provider-neutral client used to characterize host validation."""

    async def canonicalize_ib_host(self, host: str) -> str:
        assert host in {_UFM_DEVICE_NAME, _CANONICAL_HOST}
        return _CANONICAL_HOST

    async def resolve_ib_host_site(self, host: str) -> IBHostSite:
        assert host == _UFM_DEVICE_NAME
        return IBHostSite(
            device_id="device-1",
            device_name=_UFM_DEVICE_NAME,
            device_primary_ip=_CANONICAL_HOST,
            site_id="354dae20-64ef-4a7f-b1ca-2b584d20fa94",
            site_name="site-a",
        )


@asynccontextmanager
async def _canonicalization_client() -> AsyncGenerator[_CanonicalizationClient]:
    yield _CanonicalizationClient()


def _member_inputs(input_type: Callable[..., BaseModel]) -> tuple[BaseModel, BaseModel]:
    return (
        input_type(host=_UFM_DEVICE_NAME, pkey="0x100", guids=[_GUID]),
        input_type(host=_CANONICAL_HOST, pkey="0x0100", guids=[_GUID]),
    )


def test_member_workflows_retain_identical_lock_specifications() -> None:
    """Add, update, and delete serialize on the same ordered resource fields."""
    lock_specs = [
        workflow_class.get_workflow_lock()
        for workflow_class in (
            IBPKeyMemberAddWorkflow,
            IBPKeyMemberDeleteWorkflow,
            IBPKeyMemberUpdateWorkflow,
        )
    ]

    assert all(lock_spec is not None for lock_spec in lock_specs)
    assert lock_specs[0] == lock_specs[1] == lock_specs[2]
    assert lock_specs[0].key_fields == ["host", "pkey"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("workflow_class", "inputs"),
    [
        (IBPKeyMemberAddWorkflow, _member_inputs(IBPKeyMemberAddInput)),
        (IBPKeyMemberDeleteWorkflow, _member_inputs(IBPKeyMemberDeleteInput)),
        (IBPKeyMemberUpdateWorkflow, _member_inputs(IBPKeyMemberUpdateInput)),
    ],
)
async def test_canonicalization_preserves_member_workflow_lock_keys(
    monkeypatch: pytest.MonkeyPatch,
    workflow_class: type[WorkflowMetadataMixin],
    inputs: tuple[BaseModel, BaseModel],
) -> None:
    """Equivalent UFM identifiers must retain the exact same distributed lock key."""
    monkeypatch.setattr(ib_dcim, "_dcim_workflow_client", _canonicalization_client)
    lock_spec = workflow_class.get_workflow_lock()
    assert lock_spec is not None
    assert lock_spec.key_fields == ["host", "pkey"]
    assert [workflow_input.pkey for workflow_input in inputs] == ["0x0100", "0x0100"]

    keys = []
    for workflow_input in inputs:
        canonical_input = await workflow_class.canonicalize_input(workflow_input)
        keys.append(
            build_workflow_lock_key(
                lock_spec,
                workflow_name=workflow_class.get_workflow_name(),
                namespace=workflow_class.get_workflow_namespace(),
                workflow_input=canonical_input,
            )
        )

    assert keys == [
        "wf-lock:ngc:host=10.0.0.5:pkey=0x0100",
        "wf-lock:ngc:host=10.0.0.5:pkey=0x0100",
    ]
