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
"""Infrastructure configuration screen — TLS, CNPG backup, monitoring, load balancer."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, Input, Label, RadioButton, RadioSet

from nv_config_manager_installer.schema import (
    GatewayType,
    LBProvider,
    NVConfigManagerInstallConfig,
)
from nv_config_manager_installer.tui.widgets import LabeledSwitch

_W_CNPG_BACKUP = "#cnpg-backup-enabled"

_NLB_FIELD_MAP: list[tuple[str, str]] = [
    ("type", "type"),
    ("target_type", "target-type"),
    ("name", "name"),
    ("sg", "sg"),
    ("subnets", "subnets"),
    ("ips", "ips"),
    ("dns_name", "dns-name"),
]


def _nlb_service_fields(prefix: str, label: str, cfg: object) -> ComposeResult:
    """Yield NLB fields for a single service (ZTP, DHCP, or Gateway)."""
    yield Label(f"── {label} ──", classes="field-label")
    yield Label("LB Type", classes="field-label")
    yield Input(value=getattr(cfg, "type", "external"), placeholder="external", id=f"{prefix}-type")
    yield Label("Target Type", classes="field-label")
    yield Input(
        value=getattr(cfg, "target_type", "ip"), placeholder="ip", id=f"{prefix}-target-type"
    )
    yield Label("Name", classes="field-label")
    yield Input(value=getattr(cfg, "name", ""), placeholder="my-ztp-lb", id=f"{prefix}-name")
    yield Label("Security Groups", classes="field-label")
    yield Input(value=getattr(cfg, "sg", ""), placeholder="sg-abc123, sg-def456", id=f"{prefix}-sg")
    yield Label("Subnets", classes="field-label")
    yield Input(
        value=getattr(cfg, "subnets", ""),
        placeholder="subnet-abc123, subnet-def456",
        id=f"{prefix}-subnets",
    )
    yield Label("Static IPs (optional)", classes="field-label")
    yield Input(value=getattr(cfg, "ips", ""), placeholder="10.0.0.1, 10.0.0.2", id=f"{prefix}-ips")
    yield Label("DNS Name (optional)", classes="field-label")
    yield Input(
        value=getattr(cfg, "dns_name", ""),
        placeholder="ztp-ext.example.com",
        id=f"{prefix}-dns-name",
    )


class InfraScreen(Container):
    """Infrastructure: TLS, CNPG backup, monitoring, and load balancer."""

    def __init__(self, config: NVConfigManagerInstallConfig, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._config = config

    def compose(self) -> ComposeResult:
        infra = self._config.infrastructure
        yield Label("Infrastructure", classes="section-title")
        yield Label("─" * 40, classes="section-divider")

        yield Label("Gateway API Controller", classes="field-label")
        with RadioSet(id="gateway-type"):
            yield RadioButton(
                "Envoy Gateway",
                value=infra.gateway == GatewayType.ENVOY_GATEWAY,
                id="gateway-envoy",
            )
            yield RadioButton(
                "kgateway",
                value=infra.gateway == GatewayType.KGATEWAY,
                id="gateway-kgateway",
            )
        yield LabeledSwitch(
            "Create dedicated Gateway",
            value=infra.create_gateway,
            id="infra-create-gateway",
        )
        yield Label("Gateway name", classes="field-label")
        yield Input(value=infra.gateway_name, id="infra-gateway-name")
        yield Label("Gateway namespace (blank uses application namespace)", classes="field-label")
        yield Input(value=infra.gateway_namespace, id="infra-gateway-namespace")
        yield Label("Shared Gateway listener (optional)", classes="field-label")
        yield Input(value=infra.gateway_listener, id="infra-gateway-listener")
        yield Label("GatewayClass name (blank uses controller default)", classes="field-label")
        yield Input(value=infra.gateway_class_name, id="infra-gateway-class-name")

        yield LabeledSwitch(
            "Create GatewayClass",
            value=infra.create_gateway_class,
            id="infra-create-gateway-class",
        )
        yield LabeledSwitch("Enable TLS", value=infra.tls, id="infra-tls")

        yield Label("CNPG S3 Backup", classes="field-label")
        yield LabeledSwitch(
            "Enable CNPG S3 backup",
            value=infra.cnpg_s3_backup.enabled,
            id="cnpg-backup-enabled",
        )
        with Container(id="cnpg-backup-fields"):
            yield Label("Bucket", classes="field-label")
            yield Input(
                value=infra.cnpg_s3_backup.bucket,
                placeholder="nv-config-manager-postgres-backups",
                id="cnpg-bucket",
            )
            yield Label("Path", classes="field-label")
            yield Input(value=infra.cnpg_s3_backup.path, placeholder="cnpg-backups", id="cnpg-path")
            yield Label("Endpoint", classes="field-label")
            yield Input(
                value=infra.cnpg_s3_backup.endpoint,
                placeholder="https://s3.amazonaws.com",
                id="cnpg-endpoint",
            )

        yield Label("Monitoring", classes="field-label")
        yield LabeledSwitch(
            "Enable monitoring", value=infra.monitoring.enabled, id="monitoring-enabled"
        )
        yield Label("Prometheus namespace (network policy)", classes="field-label")
        yield Input(
            value=infra.monitoring.prometheus_namespace,
            placeholder="monitoring",
            id="monitoring-prometheus-namespace",
        )
        yield LabeledSwitch(
            "Enable local observability stack (Prometheus + Alloy, dev only)",
            value=infra.monitoring.observability_enabled,
            id="monitoring-observability-enabled",
        )
        yield LabeledSwitch(
            "Enable local Redis metrics exporter "
            "(local observability enables Redis metrics automatically)",
            value=infra.monitoring.redis_metrics_enabled,
            id="monitoring-redis-metrics-enabled",
        )

        yield Label("Load Balancer", classes="field-label")
        with RadioSet(id="lb-provider"):
            yield RadioButton(
                "None", value=infra.load_balancer.provider == LBProvider.NONE, id="lb-none"
            )
            yield RadioButton(
                "MetalLB",
                value=infra.load_balancer.provider == LBProvider.METALLB,
                id="lb-metallb",
            )
            yield RadioButton(
                "Cilium",
                value=infra.load_balancer.provider == LBProvider.CILIUM,
                id="lb-cilium",
            )
            yield RadioButton(
                "AWS NLB", value=infra.load_balancer.provider == LBProvider.NLB, id="lb-nlb"
            )

        with Container(id="lb-ip-fields"):
            yield Label("ZTP LoadBalancer IP", classes="field-label")
            yield Input(value=infra.load_balancer.ztp_lb_ip, placeholder="", id="lb-ztp-ip")
            yield Label("ZTP DNS Name (optional)", classes="field-label")
            yield Input(
                value=infra.load_balancer.ztp_dns_name,
                placeholder="ztp-ext.example.com",
                id="lb-ztp-dns",
            )
            yield Label("DHCP LoadBalancer IP", classes="field-label")
            yield Input(value=infra.load_balancer.dhcp_lb_ip, placeholder="", id="lb-dhcp-ip")
            yield Label("DHCP DNS Name (optional)", classes="field-label")
            yield Input(
                value=infra.load_balancer.dhcp_dns_name,
                placeholder="dhcp-ext.example.com",
                id="lb-dhcp-dns",
            )
            yield Label("Allowed Source Prefixes", classes="field-label")
            yield Button("+ Add Prefix", id="lb-prefix-add", classes="add-button")
            yield Vertical(id="lb-prefix-list")

        with Container(id="nlb-fields"):
            yield from _nlb_service_fields("nlb-gw", "Gateway NLB", infra.load_balancer.nlb_gateway)
            yield from _nlb_service_fields("nlb-ztp", "ZTP NLB", infra.load_balancer.nlb_ztp)
            yield from _nlb_service_fields("nlb-dhcp", "DHCP NLB", infra.load_balancer.nlb_dhcp)

    def on_mount(self) -> None:
        self._toggle_cnpg_fields()
        self._toggle_lb_fields()
        self._sync_redis_metrics_state(self._config)
        self._rebuild_prefix_rows()

    def on_labeled_switch_changed(self, event: LabeledSwitch.Changed) -> None:
        if event.labeled_switch.id == "cnpg-backup-enabled":
            self._toggle_cnpg_fields()

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        if event.radio_set.id == "lb-provider":
            self._toggle_lb_fields()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "lb-prefix-add":
            self._collect_prefixes()
            self._config.infrastructure.load_balancer.allowed_prefixes.append("")
            self._rebuild_prefix_rows()
        elif bid.startswith("lb-prefix-") and bid.endswith("-remove"):
            self._handle_prefix_remove(bid)

    def _handle_prefix_remove(self, bid: str) -> None:
        self._collect_prefixes()
        try:
            idx = int(bid.split("-")[2])
            prefixes = self._config.infrastructure.load_balancer.allowed_prefixes
            if 0 <= idx < len(prefixes):
                prefixes.pop(idx)
        except (ValueError, IndexError):
            pass
        self._rebuild_prefix_rows()

    def _toggle_cnpg_fields(self) -> None:
        self.query_one("#cnpg-backup-fields").display = self.query_one(
            _W_CNPG_BACKUP, LabeledSwitch
        ).value

    def _toggle_lb_fields(self) -> None:
        is_none = self.query_one("#lb-none", RadioButton).value
        is_nlb = self.query_one("#lb-nlb", RadioButton).value
        self.query_one("#lb-ip-fields").display = not is_none and not is_nlb
        self.query_one("#nlb-fields").display = is_nlb

    def _sync_redis_metrics_state(self, config: NVConfigManagerInstallConfig) -> None:
        switch = self.query_one("#monitoring-redis-metrics-enabled", LabeledSwitch)
        switch.value = config.infrastructure.monitoring.redis_metrics_enabled
        switch.disabled = config.external_services.redis.enabled

    def _rebuild_prefix_rows(self) -> None:
        container = self.query_one("#lb-prefix-list", Vertical)
        container.remove_children()
        for i, prefix in enumerate(self._config.infrastructure.load_balancer.allowed_prefixes):
            inp = Input(value=prefix, placeholder="10.0.0.0/8", id=f"lb-prefix-{i}-val")
            inp.styles.width = "1fr"
            btn = Button("Remove", variant="error", id=f"lb-prefix-{i}-remove")
            btn.styles.width = "auto"
            btn.styles.min_width = 10
            row = Horizontal(classes="account-title-row")
            row.compose_add_child(inp)
            row.compose_add_child(btn)
            container.mount(row)

    def _collect_prefixes(self) -> None:
        prefixes: list[str] = []
        for i in range(len(self._config.infrastructure.load_balancer.allowed_prefixes)):
            try:
                val = self.query_one(f"#lb-prefix-{i}-val", Input).value.strip()
                if val:
                    prefixes.append(val)
            except Exception:
                pass
        self._config.infrastructure.load_balancer.allowed_prefixes = prefixes

    @staticmethod
    def _collect_nlb_fields(screen: InfraScreen, prefix: str, target: object) -> None:
        for field, suffix in _NLB_FIELD_MAP:
            try:
                setattr(target, field, screen.query_one(f"#{prefix}-{suffix}", Input).value)
            except (LookupError, AttributeError):
                pass

    def _sync_nlb_fields(self, prefix: str, source: object) -> None:
        for field, suffix in _NLB_FIELD_MAP:
            try:
                self.query_one(f"#{prefix}-{suffix}", Input).value = getattr(source, field, "")
            except (LookupError, AttributeError):
                pass

    def write_to_config(self, config: NVConfigManagerInstallConfig) -> None:
        infra = config.infrastructure
        infra.gateway = (
            GatewayType.KGATEWAY
            if self.query_one("#gateway-kgateway", RadioButton).value
            else GatewayType.ENVOY_GATEWAY
        )
        infra.create_gateway = self.query_one("#infra-create-gateway", LabeledSwitch).value
        infra.gateway_name = self.query_one("#infra-gateway-name", Input).value
        infra.gateway_namespace = self.query_one("#infra-gateway-namespace", Input).value
        infra.gateway_listener = self.query_one("#infra-gateway-listener", Input).value
        infra.gateway_class_name = self.query_one("#infra-gateway-class-name", Input).value
        infra.create_gateway_class = self.query_one(
            "#infra-create-gateway-class", LabeledSwitch
        ).value
        infra.tls = self.query_one("#infra-tls", LabeledSwitch).value
        infra.cnpg_s3_backup.enabled = self.query_one(_W_CNPG_BACKUP, LabeledSwitch).value
        infra.cnpg_s3_backup.bucket = self.query_one("#cnpg-bucket", Input).value
        infra.cnpg_s3_backup.path = self.query_one("#cnpg-path", Input).value
        infra.cnpg_s3_backup.endpoint = self.query_one("#cnpg-endpoint", Input).value
        infra.monitoring.enabled = self.query_one("#monitoring-enabled", LabeledSwitch).value
        try:
            infra.monitoring.prometheus_namespace = self.query_one(
                "#monitoring-prometheus-namespace", Input
            ).value
        except Exception as exc:
            self.app.notify(
                f"Invalid Prometheus namespace: {exc}",
                severity="error",
                markup=False,
            )
        infra.monitoring.observability_enabled = self.query_one(
            "#monitoring-observability-enabled", LabeledSwitch
        ).value
        infra.monitoring.redis_metrics_enabled = self.query_one(
            "#monitoring-redis-metrics-enabled", LabeledSwitch
        ).value

        lb_map = {
            "lb-none": LBProvider.NONE,
            "lb-metallb": LBProvider.METALLB,
            "lb-cilium": LBProvider.CILIUM,
            "lb-nlb": LBProvider.NLB,
        }
        for radio_id, provider in lb_map.items():
            if self.query_one(f"#{radio_id}", RadioButton).value:
                infra.load_balancer.provider = provider
                break

        infra.load_balancer.ztp_lb_ip = self.query_one("#lb-ztp-ip", Input).value
        infra.load_balancer.ztp_dns_name = self.query_one("#lb-ztp-dns", Input).value
        infra.load_balancer.dhcp_lb_ip = self.query_one("#lb-dhcp-ip", Input).value
        infra.load_balancer.dhcp_dns_name = self.query_one("#lb-dhcp-dns", Input).value
        self._collect_prefixes()
        infra.load_balancer.allowed_prefixes = list(
            self._config.infrastructure.load_balancer.allowed_prefixes
        )
        self._collect_nlb_fields(self, "nlb-gw", infra.load_balancer.nlb_gateway)
        self._collect_nlb_fields(self, "nlb-ztp", infra.load_balancer.nlb_ztp)
        self._collect_nlb_fields(self, "nlb-dhcp", infra.load_balancer.nlb_dhcp)

    def sync_from_config(self, config: NVConfigManagerInstallConfig) -> None:
        infra = config.infrastructure
        self.query_one("#gateway-envoy", RadioButton).value = (
            infra.gateway == GatewayType.ENVOY_GATEWAY
        )
        self.query_one("#gateway-kgateway", RadioButton).value = (
            infra.gateway == GatewayType.KGATEWAY
        )
        self.query_one("#infra-create-gateway", LabeledSwitch).value = infra.create_gateway
        self.query_one("#infra-gateway-name", Input).value = infra.gateway_name
        self.query_one("#infra-gateway-namespace", Input).value = infra.gateway_namespace
        self.query_one("#infra-gateway-listener", Input).value = infra.gateway_listener
        self.query_one("#infra-gateway-class-name", Input).value = infra.gateway_class_name
        self.query_one(
            "#infra-create-gateway-class", LabeledSwitch
        ).value = infra.create_gateway_class
        self.query_one("#infra-tls", LabeledSwitch).value = infra.tls
        self.query_one(_W_CNPG_BACKUP, LabeledSwitch).value = infra.cnpg_s3_backup.enabled
        self.query_one("#cnpg-bucket", Input).value = infra.cnpg_s3_backup.bucket
        self.query_one("#cnpg-path", Input).value = infra.cnpg_s3_backup.path
        self.query_one("#cnpg-endpoint", Input).value = infra.cnpg_s3_backup.endpoint
        self.query_one("#monitoring-enabled", LabeledSwitch).value = infra.monitoring.enabled
        self.query_one(
            "#monitoring-prometheus-namespace", Input
        ).value = infra.monitoring.prometheus_namespace
        self.query_one(
            "#monitoring-observability-enabled", LabeledSwitch
        ).value = infra.monitoring.observability_enabled
        self._sync_redis_metrics_state(config)

        provider_radio_map = {
            LBProvider.NONE: "#lb-none",
            LBProvider.METALLB: "#lb-metallb",
            LBProvider.CILIUM: "#lb-cilium",
            LBProvider.NLB: "#lb-nlb",
        }
        for prov, radio_id in provider_radio_map.items():
            self.query_one(radio_id, RadioButton).value = prov == infra.load_balancer.provider

        self.query_one("#lb-ztp-ip", Input).value = infra.load_balancer.ztp_lb_ip
        self.query_one("#lb-ztp-dns", Input).value = infra.load_balancer.ztp_dns_name
        self.query_one("#lb-dhcp-ip", Input).value = infra.load_balancer.dhcp_lb_ip
        self.query_one("#lb-dhcp-dns", Input).value = infra.load_balancer.dhcp_dns_name
        self._config.infrastructure.load_balancer.allowed_prefixes = list(
            infra.load_balancer.allowed_prefixes
        )
        self._rebuild_prefix_rows()
        self._sync_nlb_fields("nlb-gw", infra.load_balancer.nlb_gateway)
        self._sync_nlb_fields("nlb-ztp", infra.load_balancer.nlb_ztp)
        self._sync_nlb_fields("nlb-dhcp", infra.load_balancer.nlb_dhcp)
        self._toggle_cnpg_fields()
        self._toggle_lb_fields()

    def get_status(self, config: NVConfigManagerInstallConfig) -> str:
        return "[*]"
