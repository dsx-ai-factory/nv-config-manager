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
"""Cluster configuration screen."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, Input, Label, RadioButton, RadioSet

from nv_config_manager_installer.schema import DeploySize, NVConfigManagerInstallConfig, SiteConfig
from nv_config_manager_installer.tui.widgets import LabeledSwitch


class _SiteCard(Vertical):
    """Editable card for a single site entry (name only — vault paths live in App Secrets)."""

    def __init__(self, site: SiteConfig, index: int, **kwargs: object) -> None:
        super().__init__(**kwargs, classes="account-card")
        self._site = site
        self._index = index

    def compose(self) -> ComposeResult:
        prefix = f"site-{self._index}"
        with Horizontal(classes="account-title-row"):
            yield Label(f"Site {self._index + 1}", classes="account-header")
            yield Button(
                "Remove", variant="error", id=f"{prefix}-remove", classes="remove-button-inline"
            )
        yield Label("Name", classes="field-label-compact")
        yield Input(value=self._site.name, placeholder="dc01", id=f"{prefix}-name")

    def collect(self) -> SiteConfig:
        return SiteConfig(
            name=self.query_one(f"#site-{self._index}-name", Input).value,
            vault_path=self._site.vault_path,  # preserved; edited in App Secrets (ESO mode)
        )


class _CustomLabelRow(Horizontal):
    """Editable Kubernetes label key/value pair."""

    def __init__(self, key: str, value: str, index: int) -> None:
        super().__init__(classes="account-title-row custom-label-row")
        self._key = key
        self._value = value
        self._index = index

    def compose(self) -> ComposeResult:
        prefix = f"custom-label-{self._index}"
        yield Input(value=self._key, placeholder="include_in_slo", id=f"{prefix}-key")
        yield Input(value=self._value, placeholder="true", id=f"{prefix}-value")
        yield Button("Remove", variant="error", id=f"{prefix}-remove")

    def collect(self) -> tuple[str, str]:
        prefix = f"custom-label-{self._index}"
        return (
            self.query_one(f"#{prefix}-key", Input).value.strip(),
            self.query_one(f"#{prefix}-value", Input).value.strip(),
        )


class ClusterScreen(Container):
    """Cluster settings: hostname, environment, namespace, size, and sites."""

    def __init__(self, config: NVConfigManagerInstallConfig, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._config = config
        self._custom_label_items = list(config.cluster.custom_labels.items())

    def compose(self) -> ComposeResult:
        c = self._config.cluster
        yield Label("Cluster Configuration", classes="section-title")
        yield Label("─" * 40, classes="section-divider")

        yield Label("Hostname", classes="field-label")
        yield Input(
            value=c.hostname, placeholder="config-manager.example.com", id="cluster-hostname"
        )

        yield Label("Environment", classes="field-label")
        yield Input(value=c.environment, placeholder="local", id="cluster-environment")

        yield Label("Namespace", classes="field-label")
        yield Input(value=c.namespace, placeholder="nv-config-manager", id="cluster-namespace")

        yield Label("AWS IAM Role ARN (S3 IRSA, optional)", classes="field-label")
        yield Input(
            value=c.service_account_eks_role,
            placeholder="arn:aws:iam::123456789012:role/nv-config-manager-s3",
            id="cluster-service-account-eks-role",
        )

        yield Label("Release Name", classes="field-label")
        yield Input(
            value=c.release_name, placeholder="nv-config-manager", id="cluster-release-name"
        )

        yield LabeledSwitch(
            "Airgapped deployment",
            value=c.airgapped,
            id="cluster-airgapped",
        )

        yield Label("NVIDIA Config Manager Device Username", classes="field-label")
        yield Input(
            value=self._config.secrets.config_manager_service_username,
            placeholder="nv-config-manager",
            id="cluster-svc-username",
        )

        yield Label("Size", classes="field-label")
        with RadioSet(id="cluster-size"):
            yield RadioButton("small", value=c.size == DeploySize.SMALL, id="size-small")
            yield RadioButton("medium", value=c.size == DeploySize.MEDIUM, id="size-medium")
            yield RadioButton("large", value=c.size == DeploySize.LARGE, id="size-large")

        yield LabeledSwitch(
            "Mock device interaction (no real SSH/API calls)",
            value=c.mock_devices,
            id="cluster-mock-devices",
        )

        yield Label("")
        yield Label("Custom Labels", classes="section-title")
        yield Label("─" * 40, classes="section-divider")
        yield Label("Kubernetes labels propagated to workloads and monitoring metrics.")
        yield Button("+ Add Label", id="add-custom-label", classes="add-button")
        yield Vertical(id="custom-labels-list")

        yield Label("")
        yield Label("Sites", classes="section-title")
        yield Label("─" * 40, classes="section-divider")
        yield Label("Data centers managed by this NVIDIA Config Manager deployment.")
        yield Label(
            "Each name must match the slug of the corresponding Nautobot Location.",
            classes="field-label-compact",
        )
        yield Label(
            "Sites are used to scope per-site network secrets (device login, BGP passwords, etc.).",
            classes="field-label-compact",
        )
        yield Button("+ Add Site", id="add-site", classes="add-button")
        yield Vertical(id="sites-list")

    def on_mount(self) -> None:
        self._rebuild_custom_label_rows()
        self._rebuild_site_cards()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "add-custom-label":
            self._collect_custom_label_items()
            self._custom_label_items.append(("", ""))
            self._rebuild_custom_label_rows()
        elif bid.startswith("custom-label-") and bid.endswith("-remove"):
            self._collect_custom_label_items()
            try:
                idx = int(bid.split("-")[2])
                if 0 <= idx < len(self._custom_label_items):
                    self._custom_label_items.pop(idx)
            except (ValueError, IndexError):
                pass
            self._rebuild_custom_label_rows()
        elif bid == "add-site":
            self._collect_sites()
            self._config.sites.append(SiteConfig(name=""))
            self._rebuild_site_cards()
        elif bid.startswith("site-") and bid.endswith("-remove"):
            self._collect_sites()
            parts = bid.split("-")
            try:
                idx = int(parts[1])
                if 0 <= idx < len(self._config.sites):
                    self._config.sites.pop(idx)
            except (ValueError, IndexError):
                pass
            self._rebuild_site_cards()

    def _rebuild_custom_label_rows(self) -> None:
        container = self.query_one("#custom-labels-list", Vertical)
        container.remove_children()
        for index, (key, value) in enumerate(self._custom_label_items):
            container.mount(_CustomLabelRow(key, value, index))

    def _collect_custom_label_items(self) -> None:
        self._custom_label_items = [row.collect() for row in self.query(_CustomLabelRow)]

    def _collect_custom_labels(self) -> dict[str, str]:
        self._collect_custom_label_items()
        labels: dict[str, str] = {}
        for key, value in self._custom_label_items:
            if not key and not value:
                continue
            if key in labels:
                raise ValueError(f"duplicate custom label key: {key!r}")
            labels[key] = value
        return labels

    def _rebuild_site_cards(self) -> None:
        container = self.query_one("#sites-list", Vertical)
        container.remove_children()
        for i, site in enumerate(self._config.sites):
            container.mount(_SiteCard(site, i))

    def _collect_sites(self) -> None:
        self._config.sites = [card.collect() for card in self.query(_SiteCard)]

    def write_to_config(self, config: NVConfigManagerInstallConfig) -> None:
        """Collect widget values into the config model."""
        config.cluster.hostname = self.query_one("#cluster-hostname", Input).value
        config.cluster.environment = self.query_one("#cluster-environment", Input).value
        config.cluster.namespace = self.query_one("#cluster-namespace", Input).value
        config.cluster.service_account_eks_role = self.query_one(
            "#cluster-service-account-eks-role", Input
        ).value
        config.cluster.release_name = self.query_one("#cluster-release-name", Input).value
        config.cluster.airgapped = self.query_one("#cluster-airgapped", LabeledSwitch).value
        config.secrets.config_manager_service_username = self.query_one(
            "#cluster-svc-username", Input
        ).value

        size_map = {
            "size-small": DeploySize.SMALL,
            "size-medium": DeploySize.MEDIUM,
            "size-large": DeploySize.LARGE,
        }
        for radio_id, size_val in size_map.items():
            if self.query_one(f"#{radio_id}", RadioButton).value:
                config.cluster.size = size_val
                break

        config.cluster.mock_devices = self.query_one("#cluster-mock-devices", LabeledSwitch).value
        config.cluster.custom_labels = self._collect_custom_labels()

        self._collect_sites()
        config.sites = list(self._config.sites)

    def sync_from_config(self, config: NVConfigManagerInstallConfig) -> None:
        """Refresh widget values from the config model."""
        self._config = config
        c = config.cluster
        self._custom_label_items = list(c.custom_labels.items())
        self.query_one("#cluster-hostname", Input).value = c.hostname
        self.query_one("#cluster-environment", Input).value = c.environment
        self.query_one("#cluster-namespace", Input).value = c.namespace
        self.query_one(
            "#cluster-service-account-eks-role", Input
        ).value = c.service_account_eks_role
        self.query_one("#cluster-release-name", Input).value = c.release_name
        self.query_one("#cluster-airgapped", LabeledSwitch).value = c.airgapped
        self.query_one(
            "#cluster-svc-username", Input
        ).value = config.secrets.config_manager_service_username
        self.query_one("#cluster-mock-devices", LabeledSwitch).value = c.mock_devices

        size_radio_map = {
            DeploySize.SMALL: "#size-small",
            DeploySize.MEDIUM: "#size-medium",
            DeploySize.LARGE: "#size-large",
        }
        for size_val, radio_id in size_radio_map.items():
            self.query_one(radio_id, RadioButton).value = size_val == c.size

        self._rebuild_custom_label_rows()
        self._rebuild_site_cards()

    def get_status(self, config: NVConfigManagerInstallConfig) -> str:
        """Return sidebar status indicator."""
        if not config.cluster.hostname:
            return "[ ]"
        if config.sites and not all(s.name for s in config.sites):
            return "[!]"
        return "[*]"
