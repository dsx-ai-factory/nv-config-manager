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

from nv_config_manager_installer.schema import (
    BUILT_IN_NAUTOBOT_PROVIDER,
    ExternalNATSConfig,
    ExternalTemporalConfig,
    NATSAuthMethod,
    NVConfigManagerInstallConfig,
    TemporalAuthMethod,
)
from nv_config_manager_installer.tui.widgets import LabeledSwitch

_W_EXT_NAUTOBOT = "#ext-nautobot-enabled"
_W_EXT_NATS = "#ext-nats-enabled"
_W_EXT_REDIS = "#ext-redis-enabled"
_W_EXT_PG = "#ext-pg-enabled"
_W_EXT_TEMPORAL = "#ext-temporal-enabled"
_W_EXT_TEMPORAL_MTLS = "#ext-temporal-mtls"
_PG_HOST_PLACEHOLDER = "postgres.example.com  (leave empty to keep CNPG)"


class ExternalServicesScreen(Container):
    """Configure out-of-cluster service and database instances."""

    def __init__(self, config: NVConfigManagerInstallConfig, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._config = config

    def compose(self) -> ComposeResult:
        es = self._config.external_services
        r = es.redis
        pg = es.postgres
        temporal = es.temporal
        svc = self._config.services

        yield Label("External Services", classes="section-title")
        yield Label("─" * 40, classes="section-divider")
        yield Label(
            "Override in-cluster services with external instances. "
            "Leave disabled to use the default in-cluster deployments."
        )

        # ── Nautobot ──────────────────────────────────────────────────────
        yield Label("Nautobot", classes="field-label")
        yield LabeledSwitch(
            "Use external Nautobot",
            value=not svc.nautobot,
            id="ext-nautobot-enabled",
        )
        with Container(id="ext-nautobot-fields"):
            yield Label("Nautobot endpoint", classes="field-label")
            yield Input(
                value=self._config.dcim.server or svc.external_nautobot_url,
                placeholder="https://nautobot.example.com",
                id="ext-nautobot-url",
            )

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
                placeholder="tls://nats.example.com:4222",
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
        config_manager_stream = nats.streams.config_manager
        nautobot_stream = nats.streams.nautobot
        with Container(id="nats-topology"):
            yield Label("NATS topology", classes="field-label")
            yield Label("Queue", classes="field-label")
            yield Input(value=nats.queue, id="nats-queue")
            yield Label("Config Manager stream name", classes="field-label")
            yield Input(value=config_manager_stream.name, id="nats-cm-stream-name")
            yield Label("Config Manager API prefix", classes="field-label")
            yield Input(value=config_manager_stream.api_prefix, id="nats-cm-api-prefix")
            yield Label("Config Manager subjects (comma-separated)", classes="field-label")
            yield Input(
                value=", ".join(config_manager_stream.subjects),
                id="nats-cm-subjects",
            )
            yield Label("Render change subject", classes="field-label")
            yield Input(
                value=config_manager_stream.render_change_subject,
                id="nats-render-subject",
            )
            yield Label("Device change subject", classes="field-label")
            yield Input(
                value=config_manager_stream.device_change_subject,
                id="nats-device-subject",
            )
            yield Label("Archive subject", classes="field-label")
            yield Input(
                value=config_manager_stream.archive_subject,
                id="nats-archive-subject",
            )
            yield Label("Device consumer durable", classes="field-label")
            yield Input(
                value=config_manager_stream.consumer.name,
                id="nats-device-consumer",
            )
            yield Label("Archive consumer durable", classes="field-label")
            yield Input(
                value=config_manager_stream.archive_consumer.name,
                id="nats-archive-consumer",
            )
            yield Label("Archive delivery subject", classes="field-label")
            yield Input(
                value=config_manager_stream.archive_consumer.delivery_subject,
                id="nats-archive-delivery-subject",
            )
            yield Label("Nautobot stream name", classes="field-label")
            yield Input(value=nautobot_stream.name, id="nats-nautobot-stream-name")
            yield Label("Nautobot API prefix", classes="field-label")
            yield Input(value=nautobot_stream.api_prefix, id="nats-nautobot-api-prefix")
            yield Label("Nautobot subjects (comma-separated)", classes="field-label")
            yield Input(
                value=", ".join(nautobot_stream.subjects),
                id="nats-nautobot-subjects",
            )
            yield Label("Nautobot event subject", classes="field-label")
            yield Input(value=nautobot_stream.subject, id="nats-nautobot-subject")
            yield Label("Nautobot consumer durable", classes="field-label")
            yield Input(
                value=nautobot_stream.consumer.name,
                id="nats-nautobot-consumer",
            )

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
            yield Label("Nautobot host", classes="field-label")
            yield Input(
                value=pg.nautobot_host,
                placeholder=_PG_HOST_PLACEHOLDER,
                id="ext-pg-nautobot",
            )

    def on_mount(self) -> None:
        self._toggle_nautobot_fields()
        self._toggle_nats_fields()
        self._toggle_redis_fields()
        self._toggle_temporal_fields()
        self._toggle_pg_fields()

    def on_labeled_switch_changed(self, event: LabeledSwitch.Changed) -> None:
        sid = event.labeled_switch.id
        if sid == "ext-nautobot-enabled":
            self._toggle_nautobot_fields()
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

    def _toggle_nautobot_fields(self) -> None:
        self.query_one("#ext-nautobot-fields").display = self.query_one(
            _W_EXT_NAUTOBOT, LabeledSwitch
        ).value

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

    def _comma_values(self, widget_id: str) -> list[str]:
        return [value.strip() for value in self.query_one(widget_id, Input).value.split(",")]

    def write_to_config(self, config: NVConfigManagerInstallConfig) -> None:
        nautobot_external = self.query_one(_W_EXT_NAUTOBOT, LabeledSwitch).value
        nautobot_server = self.query_one("#ext-nautobot-url", Input).value.strip()
        config.dcim.provider = BUILT_IN_NAUTOBOT_PROVIDER
        config.services.nautobot = not nautobot_external
        config.services.external_nautobot_url = nautobot_server if nautobot_external else ""
        config.dcim.server = nautobot_server if nautobot_external else ""

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
            queue=self.query_one("#nats-queue", Input).value.strip(),
            streams={
                "config_manager": {
                    "name": self.query_one("#nats-cm-stream-name", Input).value.strip(),
                    "api_prefix": self.query_one("#nats-cm-api-prefix", Input).value.strip(),
                    "subjects": self._comma_values("#nats-cm-subjects"),
                    "render_change_subject": self.query_one(
                        "#nats-render-subject", Input
                    ).value.strip(),
                    "device_change_subject": self.query_one(
                        "#nats-device-subject", Input
                    ).value.strip(),
                    "archive_subject": self.query_one("#nats-archive-subject", Input).value.strip(),
                    "consumer": {
                        "name": self.query_one("#nats-device-consumer", Input).value.strip()
                    },
                    "archive_consumer": {
                        "name": self.query_one("#nats-archive-consumer", Input).value.strip(),
                        "delivery_subject": self.query_one(
                            "#nats-archive-delivery-subject", Input
                        ).value.strip(),
                    },
                },
                "nautobot": {
                    "name": self.query_one("#nats-nautobot-stream-name", Input).value.strip(),
                    "api_prefix": self.query_one("#nats-nautobot-api-prefix", Input).value.strip(),
                    "subjects": self._comma_values("#nats-nautobot-subjects"),
                    "subject": self.query_one("#nats-nautobot-subject", Input).value.strip(),
                    "consumer": {
                        "name": self.query_one("#nats-nautobot-consumer", Input).value.strip()
                    },
                },
            },
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
        pg.nautobot_host = self.query_one("#ext-pg-nautobot", Input).value.strip()

    def sync_from_config(self, config: NVConfigManagerInstallConfig) -> None:
        self._config = config
        es = config.external_services
        r = es.redis
        pg = es.postgres
        temporal = es.temporal
        svc = config.services
        try:
            self.query_one(_W_EXT_NAUTOBOT, LabeledSwitch).value = not svc.nautobot
            self.query_one("#ext-nautobot-url", Input).value = (
                config.dcim.server or svc.external_nautobot_url
            )
            self.query_one(_W_EXT_NATS, LabeledSwitch).value = es.nats.enabled
            self.query_one("#ext-nats-server", Input).value = es.nats.server
            self.query_one("#ext-nats-auth-method", Select).value = es.nats.auth_method.value
            self.query_one("#ext-nats-user", Input).value = es.nats.user
            self.query_one("#ext-nats-secret-name", Input).value = es.nats.secret_name
            self.query_one(
                "#ext-nats-external-secret-name", Input
            ).value = es.nats.external_secret_name
            self.query_one("#ext-nats-creds-path", Input).value = es.nats.creds_path
            config_manager_stream = es.nats.streams.config_manager
            nautobot_stream = es.nats.streams.nautobot
            self.query_one("#nats-queue", Input).value = es.nats.queue
            self.query_one("#nats-cm-stream-name", Input).value = config_manager_stream.name
            self.query_one("#nats-cm-api-prefix", Input).value = config_manager_stream.api_prefix
            self.query_one("#nats-cm-subjects", Input).value = ", ".join(
                config_manager_stream.subjects
            )
            self.query_one(
                "#nats-render-subject", Input
            ).value = config_manager_stream.render_change_subject
            self.query_one(
                "#nats-device-subject", Input
            ).value = config_manager_stream.device_change_subject
            self.query_one(
                "#nats-archive-subject", Input
            ).value = config_manager_stream.archive_subject
            self.query_one(
                "#nats-device-consumer", Input
            ).value = config_manager_stream.consumer.name
            self.query_one(
                "#nats-archive-consumer", Input
            ).value = config_manager_stream.archive_consumer.name
            self.query_one(
                "#nats-archive-delivery-subject", Input
            ).value = config_manager_stream.archive_consumer.delivery_subject
            self.query_one("#nats-nautobot-stream-name", Input).value = nautobot_stream.name
            self.query_one("#nats-nautobot-api-prefix", Input).value = nautobot_stream.api_prefix
            self.query_one("#nats-nautobot-subjects", Input).value = ", ".join(
                nautobot_stream.subjects
            )
            self.query_one("#nats-nautobot-subject", Input).value = nautobot_stream.subject
            self.query_one("#nats-nautobot-consumer", Input).value = nautobot_stream.consumer.name
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
            self.query_one("#ext-pg-nautobot", Input).value = pg.nautobot_host
        except LookupError:
            pass  # widgets may not be mounted yet (called before compose)
        self._toggle_nautobot_fields()
        self._toggle_nats_fields()
        self._toggle_redis_fields()
        self._toggle_temporal_fields()
        self._toggle_pg_fields()

    def get_status(self, config: NVConfigManagerInstallConfig) -> str:
        es = config.external_services
        if not config.services.nautobot and not (
            config.dcim.server or config.services.external_nautobot_url
        ):
            return "[!]"
        try:
            ExternalNATSConfig.model_validate(es.nats.model_dump())
        except ValueError:
            return "[!]"
        if es.nats.enabled and not es.nats.server:
            return "[!]"
        if es.nats.enabled and "replace-me.invalid" in es.nats.server:
            return "[!]"
        config_manager_stream = es.nats.streams.config_manager
        nautobot_stream = es.nats.streams.nautobot
        if not config_manager_stream.subjects or not nautobot_stream.subjects:
            return "[!]"
        nats_readiness_values = (
            es.nats.queue,
            config_manager_stream.name,
            config_manager_stream.api_prefix,
            *config_manager_stream.subjects,
            config_manager_stream.render_change_subject,
            config_manager_stream.device_change_subject,
            config_manager_stream.archive_subject,
            config_manager_stream.consumer.name,
            config_manager_stream.archive_consumer.name,
            config_manager_stream.archive_consumer.delivery_subject,
            nautobot_stream.name,
            nautobot_stream.api_prefix,
            *nautobot_stream.subjects,
            nautobot_stream.subject,
            nautobot_stream.consumer.name,
        )
        if not all(nats_readiness_values) or any(
            value.startswith("replace-me") for value in nats_readiness_values
        ):
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
        if (
            not config.services.nautobot
            or es.nats.enabled
            or es.redis.enabled
            or es.postgres.enabled
        ):
            return "[*]"
        return "[ ]"
