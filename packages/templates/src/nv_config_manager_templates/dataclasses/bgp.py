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
"""BGP Peer Dataclasses."""

from dataclasses import dataclass


@dataclass(frozen=True)
class BGPPeer:  # pylint: disable=too-many-instance-attributes
    """BGP Peer Class."""

    asn: int
    name: str
    status: str
    description: str
    peer_group: str
    peer_role: str
    peer_ipv4: str | None = None
    peer_ipv6: str | None = None
    source_vrf: str | None = None
    source_interface: str | None = None
    ttl: int | None = None


@dataclass(frozen=True)
class BGPLocalConfig:
    """Local BGP Configuration Class."""

    status: str
    asn: int
    interface: str
    vrf: str
    peers: list[BGPPeer]
