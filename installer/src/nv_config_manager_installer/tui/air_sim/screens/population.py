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
"""Provider-owned simulation population controls for the Topology screen."""

from __future__ import annotations

from textual.containers import Container

from nv_config_manager_installer.air_sim.sim_config import SimConfig


class PopulationPanel(Container):
    """Replaceable provider-owned simulation population controls."""

    def __init__(self, config: SimConfig, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._config = config

    def write_to_config(self, config: SimConfig) -> None:
        """Write provider-owned fields into the simulation config."""

    def sync_from_config(self, config: SimConfig) -> None:
        """Refresh provider-owned controls from the simulation config."""
        self._config = config

    def get_status(self, config: SimConfig) -> str:
        """Return this panel's navigation status contribution."""
        return "[*]"
