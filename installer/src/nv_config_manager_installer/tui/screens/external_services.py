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
"""External Services configuration screen — service and database host overrides."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Input, Label, Select
from textual.widgets.select import InvalidSelectValueError

from nv_config_manager_installer.providers.api import DCIMInstallerProvider
from nv_config_manager_installer.providers.registry import discover_installer_providers
from nv_config_manager_installer.schema import (
    ExternalNATSConfig,
    ExternalTemporalConfig,
    NATSAuthMethod,
    NVConfigManagerInstallConfig,
    TemporalAuthMethod,
)
from nv_config_manager_installer.tui.widgets import LabeledSwitch

_W_DCIM_PROVIDER = "#ext-dcim-provider"
_W_DCIM_LOCAL = "#ext-dcim-local"
_W_DCIM_SERVER = "#ext-dcim-server"
_W_DCIM_PANEL = "#ext-dcim-panel"
_W_EXT_NATS = "#ext-nats-enabled"
_W_EXT_REDIS = "#ext-redis-enabled"
_W_EXT_PG = "#ext-pg-enabled"
_W_EXT_TEMPORAL = "#ext-temporal-enabled"
_W_EXT_TEMPORAL_MTLS = "#ext-temporal-mtls"
_PG_HOST_PLACEHOLDER = "postgres.example.com  (leave empty to keep CNPG)"


class ProviderDCIMPanel(Container):
    """Extra DCIM fields a provider adds below the common provider controls."""

    def __init__(self, config: NVConfigManagerInstallConfig, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._config = config

    def write_to_config(self, config: NVConfigManagerInstallConfig) -> None:
        """Write provider-owned fields into ``config.dcim``."""

    def sync_from_config(self, config: NVConfigManagerInstallConfig) -> None:
        """Refresh provider-owned controls from the configuration."""
        self._config = config

    def get_status(self, config: NVConfigManagerInstallConfig) -> str:
        """Return this panel's navigation status contribution."""
        return "[*]"


def _available_providers() -> dict[str, DCIMInstallerProvider]:
    """Return installed providers; the wizard degrades to the configured name on failure."""
    try:
        return discover_installer_providers()
    except Exception:  # noqa: BLE001 - provider discovery must not break the wizard
        return {}


class ExternalServicesScreen(Container):
    """Configure the DCIM provider and out-of-cluster service instances."""

    def __init__(self, config: NVConfigManagerInstallConfig, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._config = config
        self._providers = _available_providers()
        self._panel: ProviderDCIMPanel | None = None
        self._panel_provider = ""

    def _provider(self, name: str | None = None) -> DCIMInstallerProvider | None:
        return self._providers.get(name or self._config.dcim.provider)

    def _selected_provider_name(self) -> str:
        value = self.query_one(_W_DCIM_PROVIDER, Select).value
        return value if isinstance(value, str) else self._config.dcim.provider

    def compose(self) -> ComposeResult:
        es = self._config.external_services
        r = es.redis
        pg = es.postgres
        temporal = es.temporal
        dcim = self._config.dcim

        yield Label("External Services", classes="section-title")
        yield Label("─" * 40, classes="section-divider")
        yield Label(
            "Select the DCIM provider, then override in-cluster services with external "
            "instances. Leave overrides disabled to use the default in-cluster deployments."
        )

        # ── DCIM provider ──────────────────────────────────────────────────
        options = [(p.metadata.display_name, name) for name, p in self._providers.items()]
        if dcim.provider not in self._providers:
            options.append((f"{dcim.provider} (not installed)", dcim.provider))
        yield Label("DCIM Provider", classes="field-label")
        yield Select(options, value=dcim.provider, allow_blank=False, id="ext-dcim-provider")
        yield LabeledSwitch("Deploy in cluster", value=dcim.local, id="ext-dcim-local")
        with Container(id="ext-dcim-fields"):
            yield Label("DCIM endpoint", classes="field-label")
            yield Input(
                value=dcim.server,
                placeholder=self._server_placeholder(dcim.provider),
                id="ext-dcim-server",
            )
        yield Container(id="ext-dcim-panel")

        # ── NATS ──────────────────────────────────────────────────────────
        nats = es.nats
        yield Label("NATS", classes="field-label")
        yield LabeledSwitch(
            "Use external NATS",
            value=nats.enabled,
            id="ext-nats-enabled",
        )
        with Container(id="ext-nats-fields"):
            yield Label("Server URL", classes="field-label")
            yield Input(
                value=nats.server,
                placeholder="nats://nats.example.com:4222",
                id="ext-nats-server",
            )
            yield Label("Authentication", classes="field-label")
            yield Select(
                [
                    ("Password", NATSAuthMethod.PASSWORD.value),
                    ("JWT credentials", NATSAuthMethod.JWT.value),
                ],
                value=nats.auth_method.value,
                allow_blank=False,
                id="ext-nats-auth-method",
            )
            yield Label("Username (password auth)", classes="field-label")
            yield Input(value=nats.user, id="ext-nats-user")
            yield Label("Password Secret name (optional)", classes="field-label")
            yield Input(value=nats.secret_name, id="ext-nats-secret-name")
            yield Label("ExternalSecret name (optional)", classes="field-label")
            yield Input(value=nats.external_secret_name, id="ext-nats-external-secret-name")
            yield Label("Credentials path (JWT auth)", classes="field-label")
            yield Input(value=nats.creds_path, id="ext-nats-creds-path")

        # ── Redis ──────────────────────────────────────────────────────────
        yield Label("Redis", classes="field-label")
        yield LabeledSwitch("Use external Redis", value=r.enabled, id="ext-redis-enabled")
        with Container(id="ext-redis-fields"):
            yield Label("Host", classes="field-label")
            yield Input(value=r.host, placeholder="redis.example.com", id="ext-redis-host")
            yield Label("Port", classes="field-label")
            yield Input(value=str(r.port), placeholder="6379", id="ext-redis-port")
            yield LabeledSwitch("TLS / SSL", value=r.ssl, id="ext-redis-ssl")
            yield LabeledSwitch(
                "Password auth", value=r.password_auth, id="ext-redis-password-auth"
            )

        # ── Slack ──────────────────────────────────────────────────────────
        yield Label("Slack", classes="field-label")
        yield Label(
            "Slack channel for NVIDIA Config Manager notifications (requires Slack token secret).",
        )
        yield Input(
            value=es.slack.channel,
            placeholder="#nv-config-manager-notifications",
            id="ext-slack-channel",
        )

        # ── Temporal ───────────────────────────────────────────────────────
        yield Label("Temporal", classes="field-label")
        yield LabeledSwitch(
            "Use external Temporal",
            value=bool(temporal.address),
            id="ext-temporal-enabled",
        )
        with Container(id="ext-temporal-fields"):
            yield Label("gRPC address", classes="field-label")
            yield Input(
                value=temporal.address,
                placeholder="temporal.example.com:7233",
                id="ext-temporal-address",
            )
            yield Label("Namespace", classes="field-label")
            yield Input(
                value=temporal.namespace,
                placeholder="default",
                id="ext-temporal-namespace",
            )
            yield LabeledSwitch(
                "Use mTLS",
                value=temporal.auth_method == TemporalAuthMethod.MTLS,
                id="ext-temporal-mtls",
            )
            with Container(id="ext-temporal-mtls-fields"):
                yield Label("Client TLS Secret", classes="field-label")
                yield Input(
                    value=temporal.tls_secret_name,
                    placeholder="temporal-client-tls",
                    id="ext-temporal-tls-secret",
                )
                yield Label("TLS server name (optional)", classes="field-label")
                yield Input(
                    value=temporal.tls_server_name,
                    placeholder="temporal.example.com",
                    id="ext-temporal-tls-server-name",
                )

        # ── PostgreSQL ─────────────────────────────────────────────────────
        yield Label("PostgreSQL", classes="field-label")
        yield LabeledSwitch("Use external PostgreSQL", value=pg.enabled, id="ext-pg-enabled")
        with Container(id="ext-pg-fields"):
            yield Label("Port", classes="field-label")
            yield Input(value=str(pg.port), placeholder="5432", id="ext-pg-port")

            yield Label("Temporal host", classes="field-label")
            yield Input(
                value=pg.temporal_host,
                placeholder=_PG_HOST_PLACEHOLDER,
                id="ext-pg-temporal",
            )
            yield Label(
                "Temporal visibility host (optional, defaults to temporal host)",
                classes="field-label",
            )
            yield Input(
                value=pg.temporal_visibility_host,
                placeholder="",
                id="ext-pg-temporal-vis",
            )
            yield Label("Config Store host", classes="field-label")
            yield Input(
                value=pg.config_store_host,
                placeholder=_PG_HOST_PLACEHOLDER,
                id="ext-pg-config-store",
            )
            yield Label("DHCP host", classes="field-label")
            yield Input(
                value=pg.dhcp_host,
                placeholder=_PG_HOST_PLACEHOLDER,
                id="ext-pg-dhcp",
            )
            yield Label("DCIM host", classes="field-label")
            yield Input(
                value=pg.dcim_host,
                placeholder=_PG_HOST_PLACEHOLDER,
                id="ext-pg-dcim",
            )

    def _server_placeholder(self, name: str) -> str:
        provider = self._provider(name)
        return provider.metadata.server_placeholder if provider else "https://dcim.example.com"

    def _mount_provider_panel(self, name: str) -> None:
        """Replace the provider panel when the selected provider changes."""
        if name == self._panel_provider:
            return
        container = self.query_one(_W_DCIM_PANEL, Container)
        container.remove_children()
        self._panel = None
        self._panel_provider = name
        provider = self._provider(name)
        if provider is not None and provider.tui.dcim_panel is not None:
            self._panel = provider.tui.dcim_panel(self._config, id="ext-dcim-provider-panel")
            container.mount(self._panel)

    def _apply_provider_capabilities(self, name: str) -> None:
        provider = self._provider(name)
        local_switch = self.query_one(_W_DCIM_LOCAL, LabeledSwitch)
        if provider is not None:
            if not provider.metadata.supports_local:
                local_switch.value = False
            elif not provider.metadata.supports_external:
                local_switch.value = True
            local_switch.disabled = not (
                provider.metadata.supports_local and provider.metadata.supports_external
            )
        self.query_one(_W_DCIM_SERVER, Input).placeholder = self._server_placeholder(name)
        self._toggle_dcim_fields()

    def on_mount(self) -> None:
        self._apply_provider_capabilities(self._config.dcim.provider)
        self._mount_provider_panel(self._config.dcim.provider)
        self._toggle_nats_fields()
        self._toggle_redis_fields()
        self._toggle_temporal_fields()
        self._toggle_pg_fields()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "ext-dcim-provider" or not isinstance(event.value, str):
            return
        name = event.value
        self._apply_provider_capabilities(name)
        self._mount_provider_panel(name)
        app = self.app
        if hasattr(app, "set_provider") and name != self._config.dcim.provider:
            self._switch_provider(self._config, name)
            app.call_later(app.set_provider, name)

    def on_labeled_switch_changed(self, event: LabeledSwitch.Changed) -> None:
        sid = event.labeled_switch.id
        if sid == "ext-dcim-local":
            self._toggle_dcim_fields()
        elif sid == "ext-nats-enabled":
            self._toggle_nats_fields()
        elif sid == "ext-redis-enabled":
            self._toggle_redis_fields()
        elif sid == "ext-temporal-enabled":
            self._toggle_temporal_fields()
        elif sid == "ext-temporal-mtls":
            self._toggle_temporal_tls_fields()
        elif sid == "ext-pg-enabled":
            self._toggle_pg_fields()

    def _toggle_dcim_fields(self) -> None:
        self.query_one("#ext-dcim-fields").display = not self.query_one(
            _W_DCIM_LOCAL, LabeledSwitch
        ).value

    @staticmethod
    def _switch_provider(config: NVConfigManagerInstallConfig, name: str) -> None:
        """Reset provider-owned DCIM values when a different provider is selected."""
        if config.dcim.provider == name:
            return
        config.dcim.provider = name
        config.dcim.settings = {}
        config.dcim.token_secret_name = ""
        config.dcim.event_stream = ""
        config.dcim.event_subject = ""

    def _toggle_nats_fields(self) -> None:
        self.query_one("#ext-nats-fields").display = self.query_one(
            _W_EXT_NATS, LabeledSwitch
        ).value

    def _toggle_redis_fields(self) -> None:
        self.query_one("#ext-redis-fields").display = self.query_one(
            _W_EXT_REDIS, LabeledSwitch
        ).value

    def _toggle_temporal_fields(self) -> None:
        enabled = self.query_one(_W_EXT_TEMPORAL, LabeledSwitch).value
        self.query_one("#ext-temporal-fields").display = enabled
        self._toggle_temporal_tls_fields()

    def _toggle_temporal_tls_fields(self) -> None:
        enabled = self.query_one(_W_EXT_TEMPORAL, LabeledSwitch).value
        mtls = self.query_one(_W_EXT_TEMPORAL_MTLS, LabeledSwitch).value
        self.query_one("#ext-temporal-mtls-fields").display = enabled and mtls

    def _toggle_pg_fields(self) -> None:
        self.query_one("#ext-pg-fields").display = self.query_one(_W_EXT_PG, LabeledSwitch).value

    def _safe_int(self, widget_id: str, default: int) -> int:
        try:
            return int(self.query_one(widget_id, Input).value.strip())
        except (ValueError, LookupError):
            return default

    def write_to_config(self, config: NVConfigManagerInstallConfig) -> None:
        name = self._selected_provider_name()
        self._switch_provider(config, name)
        local = self.query_one(_W_DCIM_LOCAL, LabeledSwitch).value
        server = self.query_one(_W_DCIM_SERVER, Input).value.strip()
        config.dcim.local = local
        config.dcim.server = "" if local else server
        if self._panel is not None:
            self._panel.write_to_config(config)

        es = config.external_services
        es.nats = ExternalNATSConfig(
            enabled=self.query_one(_W_EXT_NATS, LabeledSwitch).value,
            server=self.query_one("#ext-nats-server", Input).value.strip(),
            auth_method=NATSAuthMethod(self.query_one("#ext-nats-auth-method", Select).value),
            user=self.query_one("#ext-nats-user", Input).value.strip(),
            secret_name=self.query_one("#ext-nats-secret-name", Input).value.strip(),
            external_secret_name=self.query_one(
                "#ext-nats-external-secret-name", Input
            ).value.strip(),
            creds_path=self.query_one("#ext-nats-creds-path", Input).value.strip(),
        )
        r = es.redis
        r.enabled = self.query_one(_W_EXT_REDIS, LabeledSwitch).value
        r.host = self.query_one("#ext-redis-host", Input).value.strip()
        r.port = self._safe_int("#ext-redis-port", 6379)
        r.ssl = self.query_one("#ext-redis-ssl", LabeledSwitch).value
        r.password_auth = self.query_one("#ext-redis-password-auth", LabeledSwitch).value

        es.slack.channel = self.query_one("#ext-slack-channel", Input).value.strip()

        temporal_enabled = self.query_one(_W_EXT_TEMPORAL, LabeledSwitch).value
        es.temporal = ExternalTemporalConfig(
            address=(
                self.query_one("#ext-temporal-address", Input).value.strip()
                if temporal_enabled
                else ""
            ),
            namespace=self.query_one("#ext-temporal-namespace", Input).value.strip() or "default",
            auth_method=(
                TemporalAuthMethod.MTLS
                if temporal_enabled and self.query_one(_W_EXT_TEMPORAL_MTLS, LabeledSwitch).value
                else TemporalAuthMethod.NONE
            ),
            tls_secret_name=self.query_one("#ext-temporal-tls-secret", Input).value.strip(),
            tls_server_name=self.query_one("#ext-temporal-tls-server-name", Input).value.strip(),
        )

        pg = es.postgres
        pg.enabled = self.query_one(_W_EXT_PG, LabeledSwitch).value
        pg.port = self._safe_int("#ext-pg-port", 5432)
        pg.temporal_host = self.query_one("#ext-pg-temporal", Input).value.strip()
        pg.temporal_visibility_host = self.query_one("#ext-pg-temporal-vis", Input).value.strip()
        pg.config_store_host = self.query_one("#ext-pg-config-store", Input).value.strip()
        pg.dhcp_host = self.query_one("#ext-pg-dhcp", Input).value.strip()
        pg.dcim_host = self.query_one("#ext-pg-dcim", Input).value.strip()

    def sync_from_config(self, config: NVConfigManagerInstallConfig) -> None:
        self._config = config
        es = config.external_services
        r = es.redis
        pg = es.postgres
        temporal = es.temporal
        try:
            try:
                self.query_one(_W_DCIM_PROVIDER, Select).value = config.dcim.provider
            except InvalidSelectValueError:
                pass  # provider is not installed; keep the current selection
            self.query_one(_W_DCIM_LOCAL, LabeledSwitch).value = config.dcim.local
            self.query_one(_W_DCIM_SERVER, Input).value = config.dcim.server
            self._apply_provider_capabilities(config.dcim.provider)
            self._mount_provider_panel(config.dcim.provider)
            if self._panel is not None:
                self._panel.sync_from_config(config)
            self.query_one(_W_EXT_NATS, LabeledSwitch).value = es.nats.enabled
            self.query_one("#ext-nats-server", Input).value = es.nats.server
            self.query_one("#ext-nats-auth-method", Select).value = es.nats.auth_method.value
            self.query_one("#ext-nats-user", Input).value = es.nats.user
            self.query_one("#ext-nats-secret-name", Input).value = es.nats.secret_name
            self.query_one(
                "#ext-nats-external-secret-name", Input
            ).value = es.nats.external_secret_name
            self.query_one("#ext-nats-creds-path", Input).value = es.nats.creds_path
            self.query_one(_W_EXT_REDIS, LabeledSwitch).value = r.enabled
            self.query_one("#ext-redis-host", Input).value = r.host
            self.query_one("#ext-redis-port", Input).value = str(r.port)
            self.query_one("#ext-redis-ssl", LabeledSwitch).value = r.ssl
            self.query_one("#ext-redis-password-auth", LabeledSwitch).value = r.password_auth
            self.query_one("#ext-slack-channel", Input).value = es.slack.channel
            self.query_one(_W_EXT_TEMPORAL, LabeledSwitch).value = bool(temporal.address)
            self.query_one("#ext-temporal-address", Input).value = temporal.address
            self.query_one("#ext-temporal-namespace", Input).value = temporal.namespace
            self.query_one(_W_EXT_TEMPORAL_MTLS, LabeledSwitch).value = (
                temporal.auth_method == TemporalAuthMethod.MTLS
            )
            self.query_one("#ext-temporal-tls-secret", Input).value = temporal.tls_secret_name
            self.query_one("#ext-temporal-tls-server-name", Input).value = temporal.tls_server_name
            self.query_one(_W_EXT_PG, LabeledSwitch).value = pg.enabled
            self.query_one("#ext-pg-port", Input).value = str(pg.port)
            self.query_one("#ext-pg-temporal", Input).value = pg.temporal_host
            self.query_one("#ext-pg-temporal-vis", Input).value = pg.temporal_visibility_host
            self.query_one("#ext-pg-config-store", Input).value = pg.config_store_host
            self.query_one("#ext-pg-dhcp", Input).value = pg.dhcp_host
            self.query_one("#ext-pg-dcim", Input).value = pg.dcim_host
        except LookupError:
            pass  # widgets may not be mounted yet (called before compose)
        self._toggle_dcim_fields()
        self._toggle_nats_fields()
        self._toggle_redis_fields()
        self._toggle_temporal_fields()
        self._toggle_pg_fields()

    def get_status(self, config: NVConfigManagerInstallConfig) -> str:
        es = config.external_services
        if not config.dcim.local and not config.dcim.server:
            return "[!]"
        if self._panel is not None and self._panel.get_status(config) == "[!]":
            return "[!]"
        if es.nats.enabled and not es.nats.server:
            return "[!]"
        if es.redis.enabled and not es.redis.host:
            return "[!]"
        if es.postgres.enabled and not any(
            [
                es.postgres.temporal_host,
                es.postgres.config_store_host,
            ]
        ):
            return "[!]"
        if not config.dcim.local or es.nats.enabled or es.redis.enabled or es.postgres.enabled:
            return "[*]"
        return "[ ]"
