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
from textual.widgets import Input, Label

from nv_config_manager_installer.schema import (
    KeaMultiThreadingConfig,
    NVConfigManagerInstallConfig,
)
from nv_config_manager_installer.tui.widgets import LabeledSwitch


def _optional_int(raw: str, label: str) -> int | None:
    """Parse a blank-able integer input; blank means unset."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{label} must be a whole number") from exc


def _int_text(value: int | None) -> str:
    return "" if value is None else str(value)


class ServicesScreen(Container):
    """Toggle services on/off and tune Kea DHCP multi-threading."""

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
            "Nautobot is configured in External Services.",
        )

        yield LabeledSwitch("Render Service", value=svc.render, id="svc-render")
        yield LabeledSwitch("ZTP Service", value=svc.ztp, id="svc-ztp")
        yield LabeledSwitch("DHCP Service", value=svc.dhcp, id="svc-dhcp")
        yield LabeledSwitch("Temporal (Workflow Engine)", value=svc.temporal, id="svc-temporal")
        yield LabeledSwitch("Config Store", value=svc.config_store, id="svc-config-store")

        kea = self._config.dhcp.kea_multi_threading
        yield Label("Kea DHCP Multi-Threading", classes="section-title")
        yield Label("─" * 40, classes="section-divider")
        yield Label(
            "Leave the fields blank to keep Kea's auto-detected worker count and 64-packet queue. "
            "CPU sizing uses the DHCP CPU request, rounded up to whole cores, and queues "
            "eight packets per thread."
        )
        yield LabeledSwitch(
            "Size threads from DHCP CPU request",
            value=kea.size_from_cpu_request,
            id="kea-size-from-cpu",
        )
        yield Label(
            "Thread pool size (0 = auto-detect; overrides CPU sizing)", classes="field-label"
        )
        yield Input(
            value=_int_text(kea.thread_pool_size), placeholder="auto", id="kea-thread-pool-size"
        )
        yield Label("Packet queue size (positive integer)", classes="field-label")
        yield Input(
            value=_int_text(kea.packet_queue_size),
            placeholder="default",
            id="kea-packet-queue-size",
        )

    def write_to_config(self, config: NVConfigManagerInstallConfig) -> None:
        config.services.render = self.query_one("#svc-render", LabeledSwitch).value
        config.services.ztp = self.query_one("#svc-ztp", LabeledSwitch).value
        config.services.dhcp = self.query_one("#svc-dhcp", LabeledSwitch).value
        config.services.temporal = self.query_one("#svc-temporal", LabeledSwitch).value
        config.services.config_store = self.query_one("#svc-config-store", LabeledSwitch).value
        # Build the model first so invalid input raises before touching the config.
        config.dhcp.kea_multi_threading = KeaMultiThreadingConfig(
            size_from_cpu_request=self.query_one("#kea-size-from-cpu", LabeledSwitch).value,
            thread_pool_size=_optional_int(
                self.query_one("#kea-thread-pool-size", Input).value, "Thread pool size"
            ),
            packet_queue_size=_optional_int(
                self.query_one("#kea-packet-queue-size", Input).value, "Packet queue size"
            ),
        )

    def sync_from_config(self, config: NVConfigManagerInstallConfig) -> None:
        svc = config.services
        self.query_one("#svc-render", LabeledSwitch).value = svc.render
        self.query_one("#svc-ztp", LabeledSwitch).value = svc.ztp
        self.query_one("#svc-dhcp", LabeledSwitch).value = svc.dhcp
        self.query_one("#svc-temporal", LabeledSwitch).value = svc.temporal
        self.query_one("#svc-config-store", LabeledSwitch).value = svc.config_store
        kea = config.dhcp.kea_multi_threading
        self.query_one("#kea-size-from-cpu", LabeledSwitch).value = kea.size_from_cpu_request
        self.query_one("#kea-thread-pool-size", Input).value = _int_text(kea.thread_pool_size)
        self.query_one("#kea-packet-queue-size", Input).value = _int_text(kea.packet_queue_size)

    def get_status(self, config: NVConfigManagerInstallConfig) -> str:
        return "[*]"
