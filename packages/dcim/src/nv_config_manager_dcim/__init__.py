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
"""Public DCIM provider SDK."""

# ruff: noqa: F401,F403

from nv_config_manager_dcim.api import (
    DCIM_PROVIDER_API_VERSION,
    DCIMClient,
    DCIMEventProvider,
    DCIMProvider,
    DCIMProviderMetadata,
    DCIMRenderEventHandler,
    DCIMRenderEventProvider,
    DCIMRenderEventRegistry,
    NautobotMCPClient,
    NautobotMCPProvider,
    ProviderSettings,
)
from nv_config_manager_dcim.errors import *  # noqa: F403
from nv_config_manager_dcim.models import *  # noqa: F403
from nv_config_manager_dcim.registry import (
    DCIM_PROVIDER_ENTRY_POINT_GROUP,
    create_dcim_client,
    discover_dcim_providers,
    get_dcim_provider,
)
from nv_config_manager_dcim.render import (
    RENDER_DATA_CACHE_SCHEMA_VERSION,
    DeviceRenderData,
    LocationRenderData,
    RenderAccessData,
    RenderBGPInstance,
    RenderBGPPeer,
    RenderConnectedDevice,
    RenderConnectedInterface,
    RenderConsoleServerPort,
    RenderCredentialReference,
    RenderData,
    RenderDataExtension,
    RenderDataRequest,
    RenderDataRequirement,
    RenderDeviceIdentity,
    RenderEndpointSet,
    RenderEvpnData,
    RenderFirmwareArtifact,
    RenderFirmwareBundle,
    RenderFirmwareComponent,
    RenderFirmwareData,
    RenderFirmwareOverrides,
    RenderInterface,
    RenderIPAddress,
    RenderIsisInterface,
    RenderL2Vni,
    RenderL2VniVrf,
    RenderL3Vni,
    RenderLocation,
    RenderLocationAddressSpace,
    RenderLocationDevice,
    RenderLocationRoutingData,
    RenderLocationTopology,
    RenderLocationVlan,
    RenderNamedEndpointSet,
    RenderNetworkData,
    RenderOtlpData,
    RenderOtlpDestination,
    RenderOverlayData,
    RenderPrefix,
    RenderPrefixSet,
    RenderRouteTarget,
    RenderRoutingData,
    RenderServicesData,
    RenderTelemetryData,
    RenderVlan,
    RenderVrf,
)
from nv_config_manager_dcim.workflow_models import (
    DeviceBayData,
    DeviceData,
    DeviceInventoryFilter,
    HostDeviceData,
    InterfaceData,
    NetworkDeviceData,
    OSImageVersions,
    Platform,
)

__all__ = [name for name in globals() if not name.startswith("_")]
