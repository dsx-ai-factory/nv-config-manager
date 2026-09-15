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
"""Tenant dataclass."""

from __future__ import annotations

import re
from dataclasses import dataclass

from nv_config_manager_dcim.render import RenderVrf

from nv_config_manager_templates.filters import FilterException


@dataclass(frozen=True)
class VRF:
    """Tenant VRF."""

    name: str
    vni: int
    export_targets: tuple[str, ...]
    import_targets: tuple[str, ...]

    @staticmethod
    def from_render_data(entry: RenderVrf) -> VRF | None:
        """Create a VRF object from normalized render data."""
        vni = entry.route_distinguisher
        export_targets = tuple(target.name for target in entry.export_targets)
        import_targets = tuple(target.name for target in entry.import_targets)

        if not (vni and re.match(r"^\*\:\d+$", vni)):
            raise FilterException(f"Invalid RD set on VRF {entry.name}.")

        # Strip site name from VRF name (e.g., "SITE_VRFNAME" becomes "VRFNAME")
        vrf_name = entry.name
        if "_" in vrf_name:
            vrf_name = vrf_name.split("_")[1]

        return VRF(
            name=vrf_name,
            vni=int(vni.replace("*:", "")),
            export_targets=export_targets,
            import_targets=import_targets,
        )
