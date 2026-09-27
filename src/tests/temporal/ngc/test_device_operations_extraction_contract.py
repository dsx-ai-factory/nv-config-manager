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
"""Freeze validation, password-rotation, and Redfish extraction contracts."""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any, Union, cast, get_args, get_origin, get_type_hints

import pytest
from nv_config_manager_dcim.workflow_models import (
    HostDeviceData,
    NetworkDeviceData,
    Platform,
)
from pydantic import BaseModel, TypeAdapter, ValidationError
from temporalio import activity

from nv_config_manager.dcim import CableStatus, CableStatusUpdate
from nv_config_manager.temporal.client.device import (
    DeviceArpTable,
    DeviceMacTable,
    DeviceNeighborData,
    InterfaceNeighborData,
)
from nv_config_manager.temporal.common.activities import REGISTERED_COMMON_ACTIVITIES
from nv_config_manager.temporal.hello_world.activities import (
    REGISTERED_ACTIVITIES as HELLO_WORLD_ACTIVITIES,
)
from nv_config_manager.temporal.ngc.activities import REGISTERED_ACTIVITIES as NGC_ACTIVITIES
from nv_config_manager.temporal.ngc.activities import bmc as bmc_activities
from nv_config_manager.temporal.ngc.activities import (
    cable_validation as cable_activities,
)
from nv_config_manager.temporal.ngc.activities import (
    device_password_rotation as password_activities,
)
from nv_config_manager.temporal.ngc.activities import (
    hardware_validation as hardware_activities,
)
from nv_config_manager_workflows.clients.redfish.models import (
    RedfishDpu,
    RedfishDpuPort,
    RedfishHost,
    RedfishNic,
    RedfishServer,
    RedfishVendor,
)
from nv_config_manager_workflows.registration.contract import activity_name

_REGISTERED_TYPES_FIXTURE = Path(__file__).parents[1] / "fixtures" / "registered_type_names.json"
_WORKFLOW_ROOT = Path(__file__).parents[3] / "nv_config_manager" / "temporal" / "ngc" / "workflows"
_WORKFLOW_PATHS = tuple(
    _WORKFLOW_ROOT / filename
    for filename in (
        "cable_validation.py",
        "cumulus_hardware_validation.py",
        "device_password_rotation.py",
        "site_password_rotation.py",
        "bmc.py",
    )
)

_ACTIVITY_CONTRACTS = (
    (
        cable_activities,
        "validate_device_neighbors",
        True,
        "activity_input",
        "ValidateDeviceNeighborsInput",
        "ValidateDeviceNeighborsResult",
    ),
    (
        cable_activities,
        "update_cable_statuses",
        True,
        "activity_input",
        "UpdateCableStatusesInput",
        "None",
    ),
    (
        cable_activities,
        "decorate_result",
        True,
        "activity_input",
        "DecorateResultActivityInput",
        "DecorateResultActivityOutput",
    ),
    (
        cable_activities,
        "format_results",
        False,
        "activity_input",
        "FormatResultsActivityInput",
        "str",
    ),
    (
        cable_activities,
        "format_device_validation_result",
        False,
        "activity_input",
        "FormatDeviceValidationResultInput",
        "str",
    ),
    *(
        (
            hardware_activities,
            name,
            False,
            "activity_input",
            "HardwareValidationInput",
            "HardwareValidationOutput",
        )
        for name in (
            "get_platform",
            "get_platform_environment_fan",
            "get_platform_environment_led",
            "get_platform_environment_psu",
            "get_platform_environment_voltage",
            "get_platform_inventory",
        )
    ),
    (
        hardware_activities,
        "create_excel_export",
        False,
        "activity_input",
        "CreateExcelInput",
        "CreateExcelOutput",
    ),
    (
        hardware_activities,
        "create_consolidated_excel_export",
        False,
        "activity_input",
        "CreateConsolidatedExcelInput",
        "CreateConsolidatedExcelOutput",
    ),
    (
        password_activities,
        "validate_password_diff",
        True,
        "activity_input",
        "ValidatePasswordDiffInput",
        "ValidatePasswordDiffOutput",
    ),
    (
        password_activities,
        "get_password_mappings",
        True,
        "activity_input",
        "GetPasswordMappingsInput",
        "GetPasswordMappingsOutput",
    ),
    (
        password_activities,
        "validate_platform_support",
        True,
        "activity_input",
        "ValidatePlatformSupportInput",
        "ValidatePlatformSupportOutput",
    ),
    (
        password_activities,
        "format_password_rotation_results",
        False,
        "activity_input",
        "FormatPasswordRotationResultsInput",
        "str",
    ),
    (
        bmc_activities,
        "discover_redfish_hosts",
        True,
        "activity_input",
        "DiscoverHostsInput",
        "DiscoverHostsOutput",
    ),
    (
        bmc_activities,
        "populate_redfish_macs",
        True,
        "activity_input",
        "PopulateRedfishMacsInput",
        "PopulateRedfishMacsOutput",
    ),
    *(
        (
            bmc_activities,
            name,
            False,
            "activity_input",
            "RedfishHostInput",
            "RedfishHostOutput",
        )
        for name in ("set_redfish_password", "power_on_host", "factory_reset_bmc")
    ),
    (
        bmc_activities,
        "get_server_details",
        False,
        "activity_input",
        "GetServerDetailsActivityInput",
        "GetServerDetailsActivityOutput",
    ),
    (
        bmc_activities,
        "get_dpu_details",
        False,
        "activity_input",
        "GetDpuDetailsActivityInput",
        "GetDpuDetailsActivityOutput",
    ),
    (
        bmc_activities,
        "update_dpu_data",
        True,
        "activity_input",
        "UpdateDpuDataActivityInput",
        "UpdateDpuDataActivityOutput",
    ),
)

_MODEL_EXPORTS = {
    cable_activities: (
        "ValidateDeviceNeighborsInput",
        "InvalidCable",
        "ValidateDeviceNeighborsResult",
        "UpdateCableStatusesInput",
        "CableValidationRow",
        "CableValidationResultData",
        "DecorateResultActivityInput",
        "DecorateResultActivityOutput",
        "FormatResultsActivityInput",
        "FormatDeviceValidationResultInput",
    ),
    hardware_activities: (
        "HardwareValidationInput",
        "HardwareValidationOutput",
        "CompleteHardwareValidationOutput",
        "HardwareValidationResult",
        "CreateExcelInput",
        "CreateExcelOutput",
        "CreateConsolidatedExcelInput",
        "CreateConsolidatedExcelOutput",
    ),
    password_activities: (
        "ValidatePasswordDiffInput",
        "ValidatePasswordDiffOutput",
        "GetPasswordMappingsInput",
        "GetPasswordMappingsOutput",
        "ValidatePlatformSupportInput",
        "ValidatePlatformSupportOutput",
        "FormatPasswordRotationResultsInput",
    ),
    bmc_activities: (
        "DiscoverHostsInput",
        "DiscoverHostsOutput",
        "PopulateRedfishMacsInput",
        "PopulateRedfishMacsOutput",
        "RedfishHostInput",
        "RedfishHostOutput",
        "GetServerDetailsActivityInput",
        "GetServerDetailsActivityOutput",
        "GetDpuDetailsActivityInput",
        "GetDpuDetailsActivityOutput",
        "UpdateDpuDataActivityInput",
        "UpdateDpuDataActivityOutput",
    ),
}

_PROVIDER_MODEL_EXPORTS = {
    "CableStatus": CableStatus,
    "CableStatusUpdate": CableStatusUpdate,
    "RedfishHost": RedfishHost,
    "RedfishServer": RedfishServer,
    "RedfishDpu": RedfishDpu,
    "RedfishDpuPort": RedfishDpuPort,
    "RedfishVendor": RedfishVendor,
}

_EXPECTED_MODEL_SCHEMA_HASHES = {
    "ValidateDeviceNeighborsInput": "0f436402e7b68e75bec33788a36b3fca4cbe4e70d8c53d26424e5799d82992d2",
    "InvalidCable": "d15a6a3da39a17226eb58d15c7c1e092eb2afbbe49c28949acd8ae1ccd09f66e",
    "ValidateDeviceNeighborsResult": "e8fed52cbc76e91b2dd37bf76d84f22f131d48014f53dbf5ddc31a99f6781806",
    "UpdateCableStatusesInput": "6abc8fc03c6c8764c28b0a100b79f138c567752856f1f4fba43a7ae82c605fc7",
    "CableValidationRow": "1c8fda521ae244b52b6d61fc6510bd5616204d9a57f4427bd278347b78d57e34",
    "CableValidationResultData": "968243c33faf7a262e721cda42975308039130c2a646627ee8388a6aa8866146",
    "DecorateResultActivityInput": "2c734207b080cd5dbc2846ef7480883c3067e73f64e5d06a424f8de024245b6d",
    "DecorateResultActivityOutput": "59512feab7f539944223016a4945a7e9eae50869cd2eb65953c98aeab8c93906",
    "FormatResultsActivityInput": "2de7576eb07caf2eefc79e3f4c594347ff434e6e409bf6048c415ef72151867c",
    "FormatDeviceValidationResultInput": "d7a67e32c89a708442081400c642868509d502fc2e5c29a92bcb8f37090ddd3d",
    "CableStatus": "a02fb0955a0436f3021ddf57b68bca760b2d0344d5fe254f1ec1d7e2eafb5639",
    "CableStatusUpdate": "521ef7b359d423a2ed853c61f8f49c4f338249aa342f04e71bef0e4bbc0c857a",
    "HardwareValidationInput": "43cd515d21d066a6b5e8adf7ea6c1c0db0f5496678fa75fb621aebf3c7be3d32",
    "HardwareValidationOutput": "77e127c1c8573ee2ee806509c6761686d5272cdbe6848ea5614ddf2e8afb8633",
    "CompleteHardwareValidationOutput": "b227439e9792f2cb173338435f89275c60e0d72430f327ded80d522752ec64c0",
    "HardwareValidationResult": "42a441c406c460745fa864062c1c136c6d5fa95103e3a9dd21cda543931a74b2",
    "CreateExcelInput": "5d640da114bae906f218b5bc19e575af3fed0b17d07ea5ef8fe2d5629740dedd",
    "CreateExcelOutput": "b8a99cae42ccf5e7c4277352b7015a63887af9a20a804cbbbe2d92f0b2e44fb4",
    "CreateConsolidatedExcelInput": "fa3172b57ab5a6fd410cdd9c490800829abc13c44c05dcdee4a9a97b0569f2a1",
    "CreateConsolidatedExcelOutput": "0026504bd0b289cd888a0df968505d1486682644621923140e954befbc0e904d",
    "ValidatePasswordDiffInput": "f32726be2f86a1816367cabdacd3df5bb522b5904875e6cbfd477a67ad461ddf",
    "ValidatePasswordDiffOutput": "dfc2930050ef34f0d1b0e032163672e3d3504963c695f534a33f68c4e4ae7b50",
    "GetPasswordMappingsInput": "5e43ac0ee1ce7b1540558e0f300e4db163d8fa8819f4a739284e0331a08710c1",
    "GetPasswordMappingsOutput": "3f61def105356de85980fae322c5e1dc518e6262c4880826af8b045b4f5d7a54",
    "ValidatePlatformSupportInput": "e5c4b46adcf84d6ca5c75eb4ca0bd981b75bff247bb6bb9c228720f2652f732e",
    "ValidatePlatformSupportOutput": "9b56d819ceac28e3f35cf1bf6970506dd251e41c5874beb9d98a29d751322d39",
    "FormatPasswordRotationResultsInput": "95ebdeb8dacd18a556fb6e5faa4698d8fb2f8a87f2ff17e36c5c27d39e09eb69",
    "DiscoverHostsInput": "c32f9a88dcdf055af91e1e6e668a02f08b00c10f8943ae511cc549934407f3cd",
    "DiscoverHostsOutput": "5ad3a7e56f6506bb1383fd01ab5f2c56fc54cd244b9c5341d8d744ea8a3af3ed",
    "PopulateRedfishMacsInput": "463f2f5066173285dcae9316f592e572957fe0600f8c2602a462d15ed94612b5",
    "PopulateRedfishMacsOutput": "3926aaf4095b7a6659eb4d6feb9ffcb9aa763cc96f315a40515b69136f68c6b3",
    "RedfishHostInput": "2602317d587472d1fb531bcfcde1b1c8931f0ae58a11e4a8a3ab14274c317ca5",
    "RedfishHostOutput": "8c14df9a53af034a23c42871c6d240b890435d754658f8e908a4099b2502e8be",
    "GetServerDetailsActivityInput": "7184bfa2864b004d312848d770efca43cd706bcadeb68acc3f342ef49296dcd6",
    "GetServerDetailsActivityOutput": "70b098659a3611c41734d2ed3d715a4da6cd6cb7810a0b44d604537509c2a56e",
    "GetDpuDetailsActivityInput": "6ac47ba8d2fd87caaa446c048804e62e69be5321a226d2bd7e8150d13211f808",
    "GetDpuDetailsActivityOutput": "55c53d31c69f06da8cff085e13b4dd739076624ffa7b0d79c95f6914fec292b2",
    "UpdateDpuDataActivityInput": "8ff6e0cc026ec107a34ee2f75485f602438d3059e0f399f677c3fa13a81b7db2",
    "UpdateDpuDataActivityOutput": "de0a66271d1fb6b491d0508dac93648884be65903eb37e9c2c30d02840ea28e7",
    "RedfishHost": "959c1fce53a53338ad95017becde68917b954340c8945c62353afbf6c37c9b26",
    "RedfishServer": "3771d6ea35aaa92ae470c2096d9644932bb65b9884e533c167ffb724bc17019c",
    "RedfishDpu": "8b4068aafdb7cae25d1cee15e4eb74c979447d4d0beab98201921cbbd353fc10",
    "RedfishDpuPort": "cb2a1e6c389b50473968b7168d629a7d5bc88a3c9bbcae1e85ce05f46a39d94b",
    "RedfishVendor": "beb4ac4433ec8f77994fba980fbb2a487db2ada48cf52f1b92de38ecdb3d3c81",
}

_EXPECTED_WORKFLOW_CALL_COUNT = 24
_EXPECTED_WORKFLOW_CALL_HASH = "65450ba87ef37c12281350d11a022e7547d1bc6ad44eaa73b73be75e88caa219"
_ACTIVITIES_WITHOUT_WORKFLOW_CALLS = {"create_excel_export"}
_EXPECTED_PAYLOAD_HASHES = {
    "ValidateDeviceNeighborsInput": "466ea94c7da34fc6ce4654698393086373d5419020f39b4937ac7bad3893f94d",
    "InvalidCable": "5f04b68a60a35ed0811fdd323fa118bbc60f6125c470845b78a0597c568a0f37",
    "ValidateDeviceNeighborsResult": "2d1780bcdd2daaefc5e9a3c0f7c128c2acee4b28c74fba52c87252ca895a50be",
    "UpdateCableStatusesInput": "518aa02fd961ddd8c3d328f28a18b3f34463871d6eb71293da70c80dafbb8358",
    "CableValidationRow": "fc07516e467ba62a53adaabdeb021871f665ee3c1dc7ad5b6fab7b6934343d14",
    "CableValidationResultData": "380cb6f377e18e87b7069a6c0e23f87d19a53e1752dad148d9587013875d65af",
    "DecorateResultActivityInput": "16de36eae05e2d3e0b44f0bf30e2f1e2af0a2596c801041d93f579d99c6685ee",
    "DecorateResultActivityOutput": "16de36eae05e2d3e0b44f0bf30e2f1e2af0a2596c801041d93f579d99c6685ee",
    "FormatResultsActivityInput": "3ae9eb80fc050b55eeb6b8983196208696adfc22c45b5d5d38ce098e2433e124",
    "FormatDeviceValidationResultInput": "a693d0e9c8141f92cc6a39e6883221124fa3e7553c6619595de05404bbcb3cf0",
    "CableStatusUpdate": "bf13fa277c4c51e960c5262ffd0bac388698d89f336178cdfecba52bdad6b0e0",
    "HardwareValidationInput": "0c21000fbd2dc92a50d8b2f3ae4c35a335af18240afc332adf60e9a4249c721b",
    "HardwareValidationOutput": "adffb64ccda5e3b7bd5c9ab0315516479b978e98c6cd6e8883d5f2a5eace0386",
    "CompleteHardwareValidationOutput": "bfe8d47ae661fe8121adfd0315a5408d5f88fa740c0f88dbfa17ca8fcae68ddb",
    "HardwareValidationResult": "5029ff65fcd28f7a7767ff8e64a04d683ae19d4c34f5a4b48e478fd35ceade68",
    "CreateExcelInput": "f6b550c5eb8a0beead6273a789218a7c664943c9cdb2c5247c658c04af3b03ea",
    "CreateExcelOutput": "bd85376c76d2af1581e43eed84ac242a5e686359907491b36d94dd59bb6bf1bd",
    "CreateConsolidatedExcelInput": "f04783b0b2dbc6b88da34f928c052e6e384705825a1578ad005b7d6ecf2e1002",
    "CreateConsolidatedExcelOutput": "6050deec22f2965e0252b23f8caa607034b2b69c52e4f247cb7d717ab589e8fb",
    "ValidatePasswordDiffInput": "f3f88821ee987b49beaf04302b0f29bb9dcaa14c57dc6946a4dc0c94bac99c2e",
    "ValidatePasswordDiffOutput": "ac3390032a797c342bf5923823d3a7fd477668eca3c1cf40918c6ac067e41240",
    "GetPasswordMappingsInput": "46591cc3a73746f20e7728b1177574837044c92b40fe287e4f1cf3afe447a56c",
    "GetPasswordMappingsOutput": "128923ad7914fd0d38d80e8b7512919e556f5bef453ba038ac39046b7936583b",
    "ValidatePlatformSupportInput": "f2dd6665b9a28c041ddbef2972e8d31127a0e84065f8a7f7a64186f4f5b7b539",
    "ValidatePlatformSupportOutput": "9720f4506b61c3cd0e1c48c057240c84e76acebffd749262d2267214d9faef5e",
    "FormatPasswordRotationResultsInput": "274e5b2489e5df4a4e3ec4637cac43f39384443f9836d565877e1e16a0f01280",
    "DiscoverHostsInput": "552f342474b25919819b83f7a8cebf74b15d77b33bf7aed45b672d0868e1c70f",
    "DiscoverHostsOutput": "6b4d5f9e55371ac0b7f0b61a41c7cfc1066c3625737611f2f70bfc3c2c1319c2",
    "PopulateRedfishMacsInput": "1dc2793c048d87e7941dda750954735944913a54c8d1c7d859c1be2c596a714b",
    "PopulateRedfishMacsOutput": "6b4d5f9e55371ac0b7f0b61a41c7cfc1066c3625737611f2f70bfc3c2c1319c2",
    "RedfishHostInput": "5942c530cef3581d59e76c22cf22e59c5774b1e34a2aab86d7c765fa5a1dd696",
    "RedfishHostOutput": "5942c530cef3581d59e76c22cf22e59c5774b1e34a2aab86d7c765fa5a1dd696",
    "GetServerDetailsActivityInput": "16002b39c266702bd00e82053d27e5e432587607f0a4a7f3375d800d10850f94",
    "GetServerDetailsActivityOutput": "a5ab0d3559fdb174600fe84a02e145ba22d381fa0c664db9aee67421570130a5",
    "GetDpuDetailsActivityInput": "5942c530cef3581d59e76c22cf22e59c5774b1e34a2aab86d7c765fa5a1dd696",
    "GetDpuDetailsActivityOutput": "60fe7ac4ed5c5b0679e8cb34c440956e288f8284f9676a0ed9745d0fa099fcc7",
    "UpdateDpuDataActivityInput": "a5ab0d3559fdb174600fe84a02e145ba22d381fa0c664db9aee67421570130a5",
    "UpdateDpuDataActivityOutput": "6b8485528a12d98782139de983997fee4da9eeaa4526edbecc88388a185481f7",
    "RedfishHost": "e0b1a47a2bcb1050f33f35320adce8b4c07bd8b56c293a6946518ef27cf5cd89",
    "RedfishServer": "17a49610ae03c21d3a8c212b23784776f93ce6552faa906110e4465c04d53a50",
    "RedfishDpu": "c1ff609f368ec70f0b982dad858a6a176b1bfead588af32cc41868b434b0356f",
    "RedfishDpuPort": "cd94d7b0fd8741298bf850954ebd4b858710e0d535b2600eb6ce02a830d703a6",
}


def _device_data() -> NetworkDeviceData:
    """Return representative provider-neutral device data."""
    return NetworkDeviceData(
        id="device-1",
        name="LEAF-1",
        rack="rack-a",
        position=42,
        role="leaf",
        site="site-1",
        device_type="switch",
        platform=Platform.CUMULUS_LINUX,
        primary_ip4="192.0.2.1",
        primary_ip6=None,
    )


def _representative_payloads() -> dict[str, BaseModel]:
    """Build one deterministic serialized value for every device-operations model."""
    device = _device_data()
    intended_neighbor = InterfaceNeighborData(
        name="eth0",
        macs=["AA-BB-CC-DD-EE-00"],
        device_name="server-1",
        device_role="server",
        device_rack="rack-b",
        device_position=7,
    )
    actual_neighbor = InterfaceNeighborData(
        name="eth9",
        macs=["AA-BB-CC-DD-EE-99"],
        device_name="server-9",
        link_up=True,
        ts_info="check peer",
    )
    intended = DeviceNeighborData(neighbors={"swp1": intended_neighbor})
    actual = DeviceNeighborData(
        neighbors={"swp1": actual_neighbor},
        link_states={"swp1": True},
    )
    invalid = cable_activities.InvalidCable(
        intended=intended_neighbor,
        actual=actual_neighbor,
    )
    validation_result = cable_activities.ValidateDeviceNeighborsResult(
        interfaces={"swp1": invalid},
        cable_statuses={"swp1": CableStatus.INVALID},
    )
    result_data = cable_activities.CableValidationResultData(
        interfaces={"swp1": invalid},
        device=device,
    )
    devices = {device.name: result_data}
    cable_row = cable_activities.CableValidationRow(  # type: ignore[call-arg]
        start_device="leaf-1",
        start_port="swp1",
        start_rack="rack-a:u42",
        intended_end_device="server-1",
        intended_end_port="eth0",
        intended_end_rack="rack-b:u7",
        actual_end_device="server-9",
        actual_end_port="eth9",
        issue="Incorrect cabling",
        troubleshooting_info="check peer",
        id_="row-1",
    )

    host = RedfishHost(
        address="192.0.2.20",
        port=8443,
        vendor=RedfishVendor.LENOVO,
        mac="aa:bb:cc:dd:ee:ff",
    )
    dpu_port = RedfishDpuPort(name="eth0", mac="aa:bb:cc:dd:ee:11")
    dpu = RedfishDpu(
        address="192.0.2.21",
        vendor=RedfishVendor.BLUEFIELD,
        mac="aa:bb:cc:dd:ee:01",
        ports=[dpu_port],
        base_mac="aa:bb:cc:dd:ee:00",
        serial="DPU-1",
    )
    server = RedfishServer(
        address=host.address,
        port=host.port,
        vendor=host.vendor,
        mac=host.mac,
        serial="SERVER-1",
        nics=[RedfishNic(name="ConnectX", slot="4", mac=None, dpu=dpu)],
    )

    return {
        "ValidateDeviceNeighborsInput": cable_activities.ValidateDeviceNeighborsInput(
            device=device,
            intended=intended,
            actual=actual,
            mac_table=DeviceMacTable(),
            arp_table=DeviceArpTable(),
        ),
        "InvalidCable": invalid,
        "ValidateDeviceNeighborsResult": validation_result,
        "UpdateCableStatusesInput": cable_activities.UpdateCableStatusesInput(
            device_id=device.id,
            cable_statuses={"swp1": CableStatus.INVALID},
            workflow_id="workflow-1",
        ),
        "CableValidationRow": cable_row,
        "CableValidationResultData": result_data,
        "DecorateResultActivityInput": cable_activities.DecorateResultActivityInput(
            devices=devices
        ),
        "DecorateResultActivityOutput": cable_activities.DecorateResultActivityOutput(
            devices=devices
        ),
        "FormatResultsActivityInput": cable_activities.FormatResultsActivityInput(
            devices=devices,
            failed_devices={"leaf-2": "unreachable"},
        ),
        "FormatDeviceValidationResultInput": (
            cable_activities.FormatDeviceValidationResultInput(
                device=device,
                validation_result=validation_result,
            )
        ),
        "CableStatusUpdate": CableStatusUpdate(
            device_id=device.id,
            interface_name="swp1",
            status=CableStatus.INVALID,
            workflow_id="workflow-1",
        ),
        "HardwareValidationInput": hardware_activities.HardwareValidationInput(device_data=device),
        "HardwareValidationOutput": hardware_activities.HardwareValidationOutput(
            info={"state": "ok", "temperature": 42.5}
        ),
        "CompleteHardwareValidationOutput": (
            hardware_activities.CompleteHardwareValidationOutput(
                device=device,
                platform={"product-name": "SN5600"},
                fan={"FAN1": {"state": "ok"}},
                led={},
                psu={},
                voltage={"rail": {"actual": 1.2}},
                inventory={},
            )
        ),
        "HardwareValidationResult": hardware_activities.HardwareValidationResult(
            success=True,
            devices_validated=2,
            total_entries=7,
            message="validated",
        ),
        "CreateExcelInput": hardware_activities.CreateExcelInput(
            command_name="fan",
            devices_data_and_results={
                device.id: {"device_data": {"name": device.name}, "command_result": {}}
            },
        ),
        "CreateExcelOutput": hardware_activities.CreateExcelOutput(
            excel_data="UEsDB",
            row_count=7,
        ),
        "CreateConsolidatedExcelInput": hardware_activities.CreateConsolidatedExcelInput(
            stage_data={"fan": {device.id: {"device_data": {"name": device.name}}}}
        ),
        "CreateConsolidatedExcelOutput": (
            hardware_activities.CreateConsolidatedExcelOutput(
                excel_data="UEsDB",
                total_row_count=7,
                worksheet_counts={"Fan": 7},
            )
        ),
        "ValidatePasswordDiffInput": password_activities.ValidatePasswordDiffInput(
            diff="nv set system aaa user admin password hash",
            username="admin",
            platform="nvos",
        ),
        "ValidatePasswordDiffOutput": password_activities.ValidatePasswordDiffOutput(
            is_valid=True,
            invalid_lines=[],
            valid_lines=["nv set system aaa user admin password hash"],
        ),
        "GetPasswordMappingsInput": password_activities.GetPasswordMappingsInput(
            device=device,
            username="admin",
        ),
        "GetPasswordMappingsOutput": password_activities.GetPasswordMappingsOutput(
            username="admin"
        ),
        "ValidatePlatformSupportInput": password_activities.ValidatePlatformSupportInput(
            platform=Platform.JUNIPER_JUNOS
        ),
        "ValidatePlatformSupportOutput": (
            password_activities.ValidatePlatformSupportOutput(normalized_platform="junos")
        ),
        "FormatPasswordRotationResultsInput": (
            password_activities.FormatPasswordRotationResultsInput(
                successful_devices={"leaf-1": {"success": True}},
                failed_devices={},
                total_devices=1,
                ui_base_url="https://temporal.example.test",
            )
        ),
        "DiscoverHostsInput": bmc_activities.DiscoverHostsInput(
            ip_range_start="192.0.2.20",
            ip_range_end="192.0.2.23",
            ips_excluded=["192.0.2.21"],
            port=8443,
        ),
        "DiscoverHostsOutput": bmc_activities.DiscoverHostsOutput(hosts=[host]),
        "PopulateRedfishMacsInput": bmc_activities.PopulateRedfishMacsInput(
            hosts=[host],
            arp_tables=[DeviceArpTable(ip_to_mac={host.address: [cast(str, host.mac)]})],
        ),
        "PopulateRedfishMacsOutput": bmc_activities.PopulateRedfishMacsOutput(hosts=[host]),
        "RedfishHostInput": bmc_activities.RedfishHostInput(host=host),
        "RedfishHostOutput": bmc_activities.RedfishHostOutput(host=host),
        "GetServerDetailsActivityInput": bmc_activities.GetServerDetailsActivityInput(
            host=host,
            nic_manufacturers=["NVIDIA", "Mellanox"],
        ),
        "GetServerDetailsActivityOutput": bmc_activities.GetServerDetailsActivityOutput(
            server=server
        ),
        "GetDpuDetailsActivityInput": bmc_activities.GetDpuDetailsActivityInput(host=host),
        "GetDpuDetailsActivityOutput": bmc_activities.GetDpuDetailsActivityOutput(dpu=dpu),
        "UpdateDpuDataActivityInput": bmc_activities.UpdateDpuDataActivityInput(server=server),
        "UpdateDpuDataActivityOutput": bmc_activities.UpdateDpuDataActivityOutput(device_data=[]),
        "RedfishHost": host,
        "RedfishServer": server,
        "RedfishDpu": dpu,
        "RedfishDpuPort": dpu_port,
    }


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


def _schema_hash(model: Any) -> str:
    """Fingerprint a complete JSON schema independently of dictionary insertion order."""
    schema = (
        model.model_json_schema()
        if isinstance(model, type) and issubclass(model, BaseModel)
        else TypeAdapter(model).json_schema()
    )
    serialized = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def _workflow_call_contracts() -> list[tuple[str, str, str]]:
    """Capture device-operations workflow calls and execution options as normalized AST."""
    contracts: list[tuple[str, str, str]] = []
    activity_names = {contract[1] for contract in _ACTIVITY_CONTRACTS}
    for path in _WORKFLOW_PATHS:
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


@pytest.mark.parametrize(
    (
        "module",
        "exported_name",
        "is_async",
        "parameter_name",
        "input_type",
        "output_type",
    ),
    _ACTIVITY_CONTRACTS,
    ids=[contract[1] for contract in _ACTIVITY_CONTRACTS],
)
def test_activity_contract_is_frozen(
    module: types.ModuleType,
    exported_name: str,
    is_async: bool,
    parameter_name: str,
    input_type: str,
    output_type: str,
) -> None:
    """Each legacy lookup path preserves its Temporal name and Python signature."""
    callable_ = getattr(module, exported_name)
    signature = inspect.signature(callable_)
    parameters = tuple(signature.parameters.values())
    type_hints = get_type_hints(callable_)

    assert module.__name__.startswith("nv_config_manager.temporal.ngc.activities.")
    assert activity._Definition.must_from_callable(callable_).name == exported_name
    assert inspect.iscoroutinefunction(callable_) is is_async
    assert len(parameters) == 1
    assert parameters[0].name == parameter_name
    assert parameters[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert parameters[0].default is inspect.Parameter.empty
    assert _type_name(type_hints[parameter_name]) == input_type
    assert _type_name(type_hints["return"]) == output_type


@pytest.mark.parametrize(
    ("model_name", "model"),
    [
        (model_name, getattr(module, model_name))
        for module, model_names in _MODEL_EXPORTS.items()
        for model_name in model_names
    ]
    + list(_PROVIDER_MODEL_EXPORTS.items()),
)
def test_model_schema_is_frozen(model_name: str, model: Any) -> None:
    """Activity-owned and provider-neutral payload schemas survive relocation unchanged."""
    assert _schema_hash(model) == _EXPECTED_MODEL_SCHEMA_HASHES[model_name]


@pytest.mark.parametrize(
    ("model_name", "model"),
    _representative_payloads().items(),
)
def test_representative_serialized_payload_is_frozen(
    model_name: str,
    model: BaseModel,
) -> None:
    """Every device-operations payload retains a representative JSON value."""
    serialized = json.dumps(
        model.model_dump(mode="json", by_alias=True),
        sort_keys=True,
        separators=(",", ":"),
    )
    assert hashlib.sha256(serialized.encode()).hexdigest() == _EXPECTED_PAYLOAD_HASHES[model_name]


def test_representative_cable_payloads_aliases_and_mutability_are_frozen() -> None:
    """Cable payload defaults, aliases, status values, and mutable behavior stay stable."""
    device = _device_data()
    intended_neighbor = InterfaceNeighborData(
        name="eth0",
        macs=["AA-BB-CC-DD-EE-00"],
        device_name="server-1",
        device_role="server",
        device_rack="rack-b",
        device_position=7,
    )
    intended = DeviceNeighborData(neighbors={"swp1": intended_neighbor})
    actual = DeviceNeighborData(link_states={"swp1": False})

    activity_input = cable_activities.ValidateDeviceNeighborsInput(
        device=device,
        intended=intended,
        actual=actual,
        mac_table=DeviceMacTable(),
        arp_table=DeviceArpTable(),
    )
    assert activity_input.model_dump(mode="json", exclude={"device"}) == {
        "intended": {
            "neighbors": {
                "swp1": {
                    "name": "eth0",
                    "macs": ["AA-BB-CC-DD-EE-00"],
                    "device_name": "server-1",
                    "device_serial": None,
                    "device_role": "server",
                    "device_rack": "rack-b",
                    "device_position": 7,
                    "link_up": None,
                    "ts_info": None,
                }
            },
            "link_states": {},
            "ts_info": {},
            "ignore": [],
            "link_state_only": [],
        },
        "actual": {
            "neighbors": {},
            "link_states": {"swp1": False},
            "ts_info": {},
            "ignore": [],
            "link_state_only": [],
        },
        "mac_table": {"by_mac": {}, "by_interface": {}},
        "arp_table": {"ip_to_mac": {}, "mac_to_ip": {}, "interface_to_mac": {}},
        "ignore_no_neighbor": False,
    }

    row = cable_activities.CableValidationRow(
        **{
            "Start Device": "=leaf-1",
            "Start Port": "swp1",
            "Start Rack": "rack-a:u42",
            "Intended End Device": "server-1",
            "Intended End Port": "eth0",
            "Intended End Rack": "rack-b:u7",
            "Actual End Device": None,
            "Actual End Port": None,
            "Issue": "Link is down.",
            "Troubleshooting Info": "check optic",
            "ID": "row-1",
        }
    )
    assert row.to_markdown() == {
        "Start Device": "=leaf-1",
        "Start Port": "swp1",
        "Intended End Device": "server-1",
        "Intended End Port": "eth0",
        "Actual End Device": None,
        "Actual End Port": None,
        "Issue": "Link is down.",
    }
    assert row.to_csv_dict() == {
        "Start Device": "'=leaf-1",
        "Start Port": "swp1",
        "Start Rack": "rack-a:u42",
        "Intended End Device": "server-1",
        "Intended End Port": "eth0",
        "Intended End Rack": "rack-b:u7",
        "Actual End Device": None,
        "Actual End Port": None,
        "Issue": "Link is down.",
        "ID": "row-1",
    }
    assert cable_activities.CableValidationRow.compute_id(row) == (
        "55463af9c9809f945b5ab4c941530048"
    )

    first = cable_activities.ValidateDeviceNeighborsResult()
    second = cable_activities.ValidateDeviceNeighborsResult()
    first.interfaces["swp1"] = cable_activities.InvalidCable(intended=intended_neighbor)
    assert second.interfaces == {}
    with pytest.raises(ValidationError):
        first.interfaces = []  # type: ignore[assignment]  # ty: ignore[invalid-assignment]

    assert [status.value for status in CableStatus] == ["Connected", "Disconnected", "Invalid"]
    assert CableStatusUpdate(
        device_id="device-1",
        interface_name="swp1",
        status=CableStatus.DISCONNECTED,
        workflow_id="workflow-1",
    ).model_dump(mode="json") == {
        "device_id": "device-1",
        "interface_name": "swp1",
        "status": "Disconnected",
        "workflow_id": "workflow-1",
    }


def test_representative_hardware_and_password_payloads_are_frozen() -> None:
    """Hardware and password models preserve field names, values, and defaults."""
    device = _device_data()
    complete = hardware_activities.CompleteHardwareValidationOutput(
        device=device,
        platform={"product-name": "SN5600"},
        fan={"FAN1": {"state": "ok"}},
        led={},
        psu={},
        voltage={"rail": {"actual": 1.2}},
        inventory={},
    )
    assert complete.model_dump(mode="json", exclude={"device"}) == {
        "platform": {"product-name": "SN5600"},
        "fan": {"FAN1": {"state": "ok"}},
        "led": {},
        "psu": {},
        "voltage": {"rail": {"actual": 1.2}},
        "inventory": {},
    }
    assert hardware_activities.HardwareValidationResult(
        success=True,
        devices_validated=2,
        total_entries=7,
        message="validated",
    ).model_dump() == {
        "success": True,
        "devices_validated": 2,
        "total_entries": 7,
        "message": "validated",
    }
    assert hardware_activities.CreateExcelInput(
        command_name="fan",
        devices_data_and_results={"device-1": {"device_data": {"name": "leaf-1"}}},
    ).model_dump() == {
        "command_name": "fan",
        "devices_data_and_results": {"device-1": {"device_data": {"name": "leaf-1"}}},
    }
    assert hardware_activities.CreateConsolidatedExcelOutput(
        excel_data="UEsDB",
        total_row_count=7,
        worksheet_counts={"Fan": 7},
    ).model_dump() == {
        "excel_data": "UEsDB",
        "total_row_count": 7,
        "worksheet_counts": {"Fan": 7},
    }

    assert password_activities.ValidatePasswordDiffOutput(
        is_valid=False,
        invalid_lines=["nv set system hostname leaf-2"],
        valid_lines=["nv set system aaa user admin password hash"],
    ).model_dump() == {
        "is_valid": False,
        "invalid_lines": ["nv set system hostname leaf-2"],
        "valid_lines": ["nv set system aaa user admin password hash"],
        "error_message": None,
    }
    assert password_activities.GetPasswordMappingsInput(
        device=device,
        username="admin",
    ).model_dump(exclude={"device"}) == {"username": "admin"}
    assert password_activities.ValidatePlatformSupportInput(
        platform=Platform.JUNIPER_JUNOS
    ).model_dump(mode="json") == {"platform": "juniper-junos"}
    assert password_activities.FormatPasswordRotationResultsInput(
        successful_devices={"leaf-1": {"success": True}},
        failed_devices={},
        total_devices=1,
        ui_base_url="https://temporal.example.test",
    ).model_dump() == {
        "successful_devices": {"leaf-1": {"success": True}},
        "failed_devices": {},
        "total_devices": 1,
        "ui_base_url": "https://temporal.example.test",
    }


def test_representative_redfish_payloads_and_model_identity_are_frozen() -> None:
    """BMC activities retain package Redfish objects and normalized serialized payloads."""
    host = RedfishHost(
        address="192.0.2.20",
        port=8443,
        vendor=RedfishVendor.LENOVO,
        mac="aa:bb:cc:dd:ee:ff",
    )
    dpu = RedfishDpu(
        address="192.0.2.21",
        vendor=RedfishVendor.BLUEFIELD,
        mac="aa:bb:cc:dd:ee:01",
        ports=[RedfishDpuPort(name="eth0", mac="aa:bb:cc:dd:ee:11")],
        base_mac="aa:bb:cc:dd:ee:00",
        serial="DPU-1",
    )
    server = RedfishServer(
        address=host.address,
        port=host.port,
        vendor=host.vendor,
        mac=host.mac,
        serial="SERVER-1",
        nics=[RedfishNic(name="ConnectX", slot="4", mac=None, dpu=dpu)],
    )

    assert bmc_activities.RedfishHost is RedfishHost
    assert bmc_activities.RedfishServer is RedfishServer
    assert bmc_activities.RedfishDpu is RedfishDpu
    assert bmc_activities.RedfishDpuPort is RedfishDpuPort
    assert bmc_activities.RedfishVendor is RedfishVendor
    assert str(host) == "Lenovo/AA-BB-CC-DD-EE-FF/192.0.2.20:8443"
    assert host.model_dump(mode="json") == {
        "address": "192.0.2.20",
        "port": 8443,
        "vendor": "Lenovo",
        "mac": "AA-BB-CC-DD-EE-FF",
    }

    discover = bmc_activities.DiscoverHostsInput(
        ip_range_start="192.0.2.20",
        ip_range_end="192.0.2.23",
        ips_excluded=["192.0.2.21"],
        port=8443,
    )
    assert discover.model_dump() == {
        "ip_range_start": "192.0.2.20",
        "ip_range_end": "192.0.2.23",
        "ips_excluded": ["192.0.2.21"],
        "port": 8443,
        "timeout": 5,
    }
    assert bmc_activities.DiscoverHostsOutput().model_dump() == {"hosts": []}
    assert bmc_activities.PopulateRedfishMacsInput(
        hosts=[host],
        arp_tables=[DeviceArpTable(ip_to_mac={host.address: [cast(str, host.mac)]})],
    ).model_dump(mode="json") == {
        "hosts": [host.model_dump(mode="json")],
        "arp_tables": [
            {
                "ip_to_mac": {"192.0.2.20": ["AA-BB-CC-DD-EE-FF"]},
                "mac_to_ip": {},
                "interface_to_mac": {},
            }
        ],
    }
    assert bmc_activities.RedfishHostOutput(host=None).model_dump() == {"host": None}
    assert bmc_activities.GetServerDetailsActivityInput(
        host=host,
        nic_manufacturers=["NVIDIA", "Mellanox"],
    ).model_dump(mode="json") == {
        "host": host.model_dump(mode="json"),
        "nic_manufacturers": ["NVIDIA", "Mellanox"],
    }
    assert bmc_activities.GetServerDetailsActivityOutput(server=server).server is server
    assert bmc_activities.GetDpuDetailsActivityOutput(dpu=dpu).dpu is dpu
    assert bmc_activities.UpdateDpuDataActivityInput(server=server).server is server
    assert bmc_activities.UpdateDpuDataActivityOutput(device_data=[]).model_dump() == {
        "device_data": []
    }

    host.mac = "aa:bb:cc:dd:ee:02"
    assert host.mac == "AA-BB-CC-DD-EE-02"
    with pytest.raises(ValidationError):
        host.port = "invalid"  # type: ignore[assignment]


def test_host_device_output_schema_accepts_provider_neutral_inventory() -> None:
    """The update-DPU result retains the shared host-inventory payload object."""
    host_device = HostDeviceData(
        id="host-1",
        name="server-1-dpu0",
        role="dpu",
        site="site-1",
        device_type="bluefield-3",
        serial="DPU-1",
        device_bays=[],
        interfaces=[],
    )
    result = bmc_activities.UpdateDpuDataActivityOutput(device_data=[host_device])
    assert result.model_dump(mode="json") == {
        "device_data": [
            {
                "id": "host-1",
                "name": "server-1-dpu0",
                "rack": None,
                "position": None,
                "role": "dpu",
                "site": "site-1",
                "device_type": "bluefield-3",
                "serial": "DPU-1",
                "device_bays": [],
                "interfaces": [],
            }
        ]
    }


def test_registered_type_name_snapshot_contains_device_operations_exactly_once() -> None:
    """Device operations leave the reconciled worker registration baseline unchanged."""
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
    device_operation_names = {contract[1] for contract in _ACTIVITY_CONTRACTS}

    assert sorted(actual) == expected
    assert len(actual) == len(set(actual)) == 121
    assert device_operation_names <= set(actual)
    assert all(actual.count(name) == 1 for name in device_operation_names)


def test_workflow_activity_arguments_and_options_are_frozen() -> None:
    """All six device-operation workflows retain their activity calls and options."""
    contracts = _workflow_call_contracts()
    serialized = json.dumps(contracts, separators=(",", ":"))
    called_names = {activity_name_ for _, activity_name_, _ in contracts}
    device_operation_names = {contract[1] for contract in _ACTIVITY_CONTRACTS}

    assert len(contracts) == _EXPECTED_WORKFLOW_CALL_COUNT
    assert hashlib.sha256(serialized.encode()).hexdigest() == _EXPECTED_WORKFLOW_CALL_HASH
    assert device_operation_names - called_names == _ACTIVITIES_WITHOUT_WORKFLOW_CALLS
