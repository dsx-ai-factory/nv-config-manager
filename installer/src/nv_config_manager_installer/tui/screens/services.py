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
"""Services toggle screen."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Label

from nv_config_manager_installer.schema import NVConfigManagerInstallConfig
from nv_config_manager_installer.tui.widgets import LabeledSwitch


class ServicesScreen(Container):
    """Toggle DHCP, Temporal, and Config Store on/off."""

    def __init__(self, config: NVConfigManagerInstallConfig, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._config = config

    def compose(self) -> ComposeResult:
        svc = self._config.services
        yield Label("Services", classes="section-title")
        yield Label("─" * 40, classes="section-divider")
        yield Label(
            "Enable or disable each service. "
            "Template Plugins and OS Images panels configure per-service storage and scheduling. "
            "The DCIM provider is configured in External Services.",
        )

        yield LabeledSwitch("Render Service", value=svc.render, id="svc-render")
        yield LabeledSwitch("ZTP Service", value=svc.ztp, id="svc-ztp")
        yield LabeledSwitch("DHCP Service", value=svc.dhcp, id="svc-dhcp")
        yield LabeledSwitch("Temporal (Workflow Engine)", value=svc.temporal, id="svc-temporal")
        yield LabeledSwitch("Config Store", value=svc.config_store, id="svc-config-store")

    def write_to_config(self, config: NVConfigManagerInstallConfig) -> None:
        config.services.render = self.query_one("#svc-render", LabeledSwitch).value
        config.services.ztp = self.query_one("#svc-ztp", LabeledSwitch).value
        config.services.dhcp = self.query_one("#svc-dhcp", LabeledSwitch).value
        config.services.temporal = self.query_one("#svc-temporal", LabeledSwitch).value
        config.services.config_store = self.query_one("#svc-config-store", LabeledSwitch).value

    def sync_from_config(self, config: NVConfigManagerInstallConfig) -> None:
        svc = config.services
        self.query_one("#svc-render", LabeledSwitch).value = svc.render
        self.query_one("#svc-ztp", LabeledSwitch).value = svc.ztp
        self.query_one("#svc-dhcp", LabeledSwitch).value = svc.dhcp
        self.query_one("#svc-temporal", LabeledSwitch).value = svc.temporal
        self.query_one("#svc-config-store", LabeledSwitch).value = svc.config_store

    def get_status(self, config: NVConfigManagerInstallConfig) -> str:
        return "[*]"
