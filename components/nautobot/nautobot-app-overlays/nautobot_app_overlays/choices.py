#  SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
#  SPDX-License-Identifier: Apache-2.0
#
#  Licensed under the Apache License, Version 2.0 (the "License")
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

"""Choices for Overlays app."""

from nautobot.apps.choices import ChoiceSet

ASSIGNABLE_CONTENT_TYPES = ["device", "interface", "rack", "vrf", "vlan", "prefix", "vxlan"]


class IsolationTypeChoices(ChoiceSet):
    """Choices for Overlay isolation_type field."""

    VXLAN_EVPN = "vxlan_evpn"
    SPECTRUM_X_VRF = "spectrum_x_vrf"
    IB_PKEY = "ib_pkey"
    IB_MKEY = "ib_mkey"
    NVLINK_PARTITION = "nvlink_partition"

    CHOICES = (
        (VXLAN_EVPN, "VXLAN/EVPN"),
        (SPECTRUM_X_VRF, "Spectrum X"),
        (IB_PKEY, "IB PKey"),
        (IB_MKEY, "IB MKey"),
        (NVLINK_PARTITION, "NVLink Partition"),
    )


# Overlay isolation types whose assignments must not carry IB PKey attributes (GUID, membership type).
GUIDLESS_ISOLATION_TYPES = (
    IsolationTypeChoices.VXLAN_EVPN,
    IsolationTypeChoices.SPECTRUM_X_VRF,
    IsolationTypeChoices.IB_MKEY,
)


class OverlayAssignmentRoleChoices(ChoiceSet):
    """Choices for OverlayAssignment role field."""

    UPLINK = "uplink"
    DOWNLINK = "downlink"
    COMPUTE = "compute"
    LEAF = "leaf"
    SPINE = "spine"
    STORAGE = "storage"

    CHOICES = (
        (UPLINK, "Uplink"),
        (DOWNLINK, "Downlink"),
        (COMPUTE, "Compute"),
        (LEAF, "Leaf"),
        (SPINE, "Spine"),
        (STORAGE, "Storage"),
    )


class PKeyMembershipTypeChoices(ChoiceSet):
    """Choices for InfiniBandPKey membership_type field."""

    FULL = "full"
    LIMITED = "limited"

    CHOICES = (
        (FULL, "Full"),
        (LIMITED, "Limited"),
    )


class VNITypeChoices(ChoiceSet):
    """Choices for VXLAN vni_type field."""

    L2_VNI = "l2"
    L3_VNI = "l3"

    CHOICES = (
        (L2_VNI, "L2 VNI"),
        (L3_VNI, "L3 VNI"),
    )
