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
"""Textual pilot tests for the TUI app."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Button, Input, Label, RadioButton

from nv_config_manager_installer.deployer import Deployer, DeployOptions, DeployStep
from nv_config_manager_installer.schema import (
    BUILT_IN_NAUTOBOT_PROVIDER,
    ClusterConfig,
    ExternalRedisConfig,
    ExternalServicesConfig,
    ImageSource,
    InfrastructureConfig,
    MonitoringConfig,
    NATSAuthMethod,
    NetworkSecretEntry,
    NVConfigManagerInstallConfig,
    PasswordSource,
    SecretsConfig,
    SecretsMethod,
    SiteConfig,
    VaultAuth,
    VaultAuthMethod,
    VaultConfig,
    VaultPathConfig,
    VaultPathsConfig,
)
from nv_config_manager_installer.tui.app import NVConfigManagerInstallerApp
from nv_config_manager_installer.tui.screens.cluster import ClusterScreen
from nv_config_manager_installer.tui.screens.deploy import DeployScreen
from nv_config_manager_installer.tui.screens.network_secrets import NetworkSecretsScreen
from nv_config_manager_installer.tui.widgets import LabeledSwitch


@pytest.mark.asyncio
async def test_app_starts_with_default_config():
    """The app should start and show the cluster screen."""
    app = NVConfigManagerInstallerApp(config=NVConfigManagerInstallConfig())
    async with app.run_test():
        assert app.active_section == "cluster"
        assert app.title == "NVCM Install Wizard"


@pytest.mark.asyncio
async def test_switch_section():
    """Clicking a nav item should switch the visible section."""
    app = NVConfigManagerInstallerApp(config=NVConfigManagerInstallConfig())
    async with app.run_test():
        app.switch_section("sso")
        assert app.active_section == "sso"
        assert app._screens["sso"].display is True
        assert app._screens["cluster"].display is False


@pytest.mark.asyncio
async def test_deploy_can_skip_vault_population():
    config = NVConfigManagerInstallConfig(secrets=SecretsConfig(method=SecretsMethod.ESO))
    app = NVConfigManagerInstallerApp(config=config)
    async with app.run_test():
        app.switch_section("deploy")
        deploy_screen = app._screens["deploy"]
        deploy_screen.query_one("#opt-populate-vault", LabeledSwitch).value = False

        assert deploy_screen._collect_deploy_options().populate_vault is False


@pytest.mark.parametrize(
    ("method", "recreate_disabled", "populate_disabled", "token_disabled"),
    [
        (SecretsMethod.KUBERNETES, False, True, True),
        (SecretsMethod.ESO, True, False, False),
    ],
)
@pytest.mark.asyncio
async def test_deploy_secret_options_follow_secret_method(
    method: SecretsMethod,
    recreate_disabled: bool,
    populate_disabled: bool,
    token_disabled: bool,
):
    config = NVConfigManagerInstallConfig(secrets=SecretsConfig(method=method))
    app = NVConfigManagerInstallerApp(config=config)

    async with app.run_test():
        app.switch_section("deploy")
        deploy_screen = app._screens["deploy"]

        assert (
            deploy_screen.query_one("#opt-recreate-secrets", LabeledSwitch).disabled
            is recreate_disabled
        )
        assert (
            deploy_screen.query_one("#opt-populate-vault", LabeledSwitch).disabled
            is populate_disabled
        )
        assert deploy_screen.query_one("#opt-openbao-token-file", Input).disabled is token_disabled


@pytest.mark.asyncio
async def test_deploy_disables_token_file_when_vault_population_is_off():
    config = NVConfigManagerInstallConfig(secrets=SecretsConfig(method=SecretsMethod.ESO))
    app = NVConfigManagerInstallerApp(config=config)

    async with app.run_test() as pilot:
        app.switch_section("deploy")
        deploy_screen = app._screens["deploy"]
        token_input = deploy_screen.query_one("#opt-openbao-token-file", Input)
        token_input.value = "/tmp/token"
        deploy_screen.query_one("#opt-populate-vault", LabeledSwitch).value = False
        await pilot.pause()

        assert token_input.disabled is True
        assert deploy_screen._collect_deploy_options().vault_token_file is None


@pytest.mark.asyncio
async def test_deploy_secret_options_refresh_after_switching_to_eso():
    app = NVConfigManagerInstallerApp(config=NVConfigManagerInstallConfig())

    async with app.run_test() as pilot:
        app.switch_section("secrets")
        app._screens["secrets"].query_one("#method-eso", RadioButton).value = True
        await pilot.pause()
        app.switch_section("deploy")
        deploy_screen = app._screens["deploy"]

        assert deploy_screen.query_one("#opt-recreate-secrets", LabeledSwitch).disabled is True
        assert deploy_screen.query_one("#opt-populate-vault", LabeledSwitch).disabled is False
        assert deploy_screen.query_one("#opt-populate-vault", LabeledSwitch).value is True
        assert deploy_screen.query_one("#opt-openbao-token-file", Input).disabled is False


@pytest.mark.asyncio
async def test_collect_config():
    """collect_config should read values from screens into the config model."""
    config = NVConfigManagerInstallConfig()
    app = NVConfigManagerInstallerApp(config=config)
    async with app.run_test():
        app.collect_config()
        assert app.config.cluster.environment == "local"


@pytest.mark.asyncio
async def test_infrastructure_loads_explicit_redis_metrics_preference():
    config = NVConfigManagerInstallConfig(
        infrastructure=InfrastructureConfig(
            monitoring=MonitoringConfig(redis_metrics_enabled=True),
        ),
    )
    app = NVConfigManagerInstallerApp(config=config)

    async with app.run_test():
        app.switch_section("infrastructure")

        switch = app._screens["infrastructure"].query_one(
            "#monitoring-redis-metrics-enabled", LabeledSwitch
        )
        assert switch.value is True
        assert switch.disabled is False
        switch.value = False
        app.collect_config()
        assert app.config.infrastructure.monitoring.redis_metrics_enabled is False


@pytest.mark.asyncio
async def test_infrastructure_preserves_explicit_redis_metrics_preference_with_observability():
    config = NVConfigManagerInstallConfig(
        infrastructure=InfrastructureConfig(
            monitoring=MonitoringConfig(
                observability_enabled=True,
                redis_metrics_enabled=False,
            ),
        ),
    )
    app = NVConfigManagerInstallerApp(config=config)

    async with app.run_test():
        app.switch_section("infrastructure")
        switch = app._screens["infrastructure"].query_one(
            "#monitoring-redis-metrics-enabled", LabeledSwitch
        )

        assert switch.value is False
        app.collect_config()
        assert app.config.infrastructure.monitoring.redis_metrics_enabled is False


@pytest.mark.asyncio
async def test_infrastructure_disables_redis_metrics_for_external_redis():
    config = NVConfigManagerInstallConfig(
        external_services=ExternalServicesConfig(
            redis=ExternalRedisConfig(enabled=True, host="redis.example.com"),
        ),
        infrastructure=InfrastructureConfig(
            monitoring=MonitoringConfig(redis_metrics_enabled=True),
        ),
    )
    app = NVConfigManagerInstallerApp(config=config)

    async with app.run_test():
        app.switch_section("infrastructure")
        switch = app._screens["infrastructure"].query_one(
            "#monitoring-redis-metrics-enabled", LabeledSwitch
        )

        assert switch.disabled is True
        assert switch.value is True
        app.collect_config()
        assert app.config.infrastructure.monitoring.redis_metrics_enabled is True


@pytest.mark.asyncio
async def test_infrastructure_monitoring_urls_sync_and_collect() -> None:
    config = NVConfigManagerInstallConfig(
        infrastructure=InfrastructureConfig(
            monitoring=MonitoringConfig(
                grafana_url="https://grafana.example.com/d/current",
                blackbox_exporter_url="blackbox-current.monitoring.svc:9115",
            ),
        ),
    )
    app = NVConfigManagerInstallerApp(config=config)

    async with app.run_test():
        app.switch_section("infrastructure")
        screen = app._screens["infrastructure"]

        assert (
            screen.query_one("#monitoring-grafana-url", Input).value
            == "https://grafana.example.com/d/current"
        )
        assert (
            screen.query_one("#monitoring-blackbox-exporter-url", Input).value
            == "blackbox-current.monitoring.svc:9115"
        )

        screen.query_one(
            "#monitoring-grafana-url", Input
        ).value = "https://grafana.example.com/d/new?var-namespace=kiwi"
        screen.query_one(
            "#monitoring-blackbox-exporter-url", Input
        ).value = "blackbox-new.monitoring.svc:9115"
        app.collect_config()

        assert (
            config.infrastructure.monitoring.grafana_url
            == "https://grafana.example.com/d/new?var-namespace=kiwi"
        )
        assert (
            config.infrastructure.monitoring.blackbox_exporter_url
            == "blackbox-new.monitoring.svc:9115"
        )


@pytest.mark.parametrize(
    ("field_id", "invalid_value"),
    [
        ("#monitoring-grafana-url", "grafana.example.com/d/overview"),
        ("#monitoring-blackbox-exporter-url", "https://blackbox.example.com:9115"),
    ],
)
@pytest.mark.asyncio
async def test_invalid_monitoring_url_blocks_navigation(field_id: str, invalid_value: str) -> None:
    config = NVConfigManagerInstallConfig()
    app = NVConfigManagerInstallerApp(config=config)

    async with app.run_test():
        app.switch_section("infrastructure")
        app._screens["infrastructure"].query_one(field_id, Input).value = invalid_value

        app.switch_section("services")

        assert app.active_section == "infrastructure"
        assert config.infrastructure.monitoring.grafana_url == ""
        assert config.infrastructure.monitoring.blackbox_exporter_url == ""


@pytest.mark.asyncio
async def test_invalid_monitoring_url_does_not_partially_update_config() -> None:
    config = NVConfigManagerInstallConfig(
        infrastructure=InfrastructureConfig(
            monitoring=MonitoringConfig(
                grafana_url="https://grafana.example.com/d/current",
                blackbox_exporter_url="blackbox-current.monitoring.svc:9115",
            ),
        ),
    )
    app = NVConfigManagerInstallerApp(config=config)

    async with app.run_test():
        app.switch_section("infrastructure")
        screen = app._screens["infrastructure"]
        screen.query_one(
            "#monitoring-grafana-url", Input
        ).value = "https://grafana.example.com/d/new"
        screen.query_one(
            "#monitoring-blackbox-exporter-url", Input
        ).value = "https://blackbox.example.com:9115"

        app.switch_section("services")

        assert app.active_section == "infrastructure"
        assert (
            config.infrastructure.monitoring.grafana_url == "https://grafana.example.com/d/current"
        )
        assert (
            config.infrastructure.monitoring.blackbox_exporter_url
            == "blackbox-current.monitoring.svc:9115"
        )


@pytest.mark.asyncio
async def test_invalid_monitoring_url_blocks_save(tmp_path) -> None:
    config = NVConfigManagerInstallConfig()
    config_path = tmp_path / "nv-config-manager-install.yaml"
    app = NVConfigManagerInstallerApp(config=config, config_path=config_path)

    async with app.run_test():
        app.switch_section("infrastructure")
        app._screens["infrastructure"].query_one(
            "#monitoring-grafana-url", Input
        ).value = "grafana.example.com/d/overview"

        app.action_save()

        assert not config_path.exists()
        assert config.infrastructure.monitoring.grafana_url == ""


@pytest.mark.asyncio
async def test_external_temporal_screen_revalidates_form_values():
    """External Temporal form values must pass model validation before saving."""
    config = NVConfigManagerInstallConfig()
    app = NVConfigManagerInstallerApp(config=config)

    async with app.run_test():
        app.switch_section("external_services")
        screen = app._screens["external_services"]
        screen.query_one("#ext-temporal-enabled", LabeledSwitch).value = True
        screen.query_one("#ext-temporal-address", Input).value = "temporal.example.com:7233"
        screen.query_one("#ext-temporal-namespace", Input).value = "remote\nnamespace"

        with pytest.raises(ValueError, match="namespace must not contain control characters"):
            screen.write_to_config(config)


@pytest.mark.asyncio
async def test_external_services_separates_nautobot_and_nats() -> None:
    config = NVConfigManagerInstallConfig()
    app = NVConfigManagerInstallerApp(config=config)

    async with app.run_test():
        app.switch_section("external_services")
        screen = app._screens["external_services"]

        assert not screen.query("#ext-dcim-provider")
        screen.query_one("#ext-nautobot-enabled", LabeledSwitch).value = False
        screen.query_one("#ext-nats-enabled", LabeledSwitch).value = True
        screen.query_one("#ext-nats-server", Input).value = "nats://nats.example.com:4222"
        screen.query_one("#ext-nats-auth-method").value = NATSAuthMethod.JWT.value
        screen.write_to_config(config)

        assert config.dcim.provider == BUILT_IN_NAUTOBOT_PROVIDER
        assert config.services.nautobot is True
        assert config.external_services.nats.enabled is True
        assert config.external_services.nats.server == "nats://nats.example.com:4222"
        assert config.external_services.nats.auth_method == NATSAuthMethod.JWT


@pytest.mark.asyncio
async def test_sites_list_in_cluster_section():
    """Sites list widget should be present in the cluster section."""
    config = NVConfigManagerInstallConfig(sites=[SiteConfig(name="dc01")])
    app = NVConfigManagerInstallerApp(config=config)
    async with app.run_test():
        app.switch_section("cluster")
        cluster_screen = app._screens["cluster"]
        assert isinstance(cluster_screen, ClusterScreen)
        sites_list = cluster_screen.query_one("#sites-list")
        assert sites_list is not None


@pytest.mark.asyncio
async def test_sites_collected_from_cluster():
    """Sites written via ClusterScreen.write_to_config should land in the config."""
    config = NVConfigManagerInstallConfig(sites=[SiteConfig(name="dc01"), SiteConfig(name="dc02")])
    app = NVConfigManagerInstallerApp(config=config)
    async with app.run_test():
        app.collect_config()
        assert len(app.config.sites) == 2
        assert app.config.sites[0].name == "dc01"
        assert app.config.sites[1].name == "dc02"


@pytest.mark.asyncio
async def test_cluster_custom_labels_sync_and_collect() -> None:
    config = NVConfigManagerInstallConfig(
        cluster=ClusterConfig(custom_labels={"production": "true", "include_in_slo": "true"})
    )
    app = NVConfigManagerInstallerApp(config=config)

    async with app.run_test() as pilot:
        app.switch_section("cluster")
        await pilot.pause()
        screen = app._screens["cluster"]

        assert screen.query_one("#custom-label-0-key", Input).value == "production"
        assert screen.query_one("#custom-label-0-value", Input).value == "true"
        screen.query_one("#custom-label-1-value", Input).value = "false"
        screen.write_to_config(config)

        assert config.cluster.custom_labels == {
            "production": "true",
            "include_in_slo": "false",
        }

        replacement = NVConfigManagerInstallConfig(
            cluster=ClusterConfig(custom_labels={"nvidia.com/environment": "prod"})
        )
        screen.sync_from_config(replacement)
        await pilot.pause()

        assert screen.query_one("#custom-label-0-key", Input).value == "nvidia.com/environment"
        assert screen.query_one("#custom-label-0-value", Input).value == "prod"
        assert not screen.query("#custom-label-1-key")


@pytest.mark.asyncio
async def test_cluster_custom_labels_add_and_remove() -> None:
    config = NVConfigManagerInstallConfig()
    app = NVConfigManagerInstallerApp(config=config)

    async with app.run_test() as pilot:
        app.switch_section("cluster")
        await pilot.pause()
        screen = app._screens["cluster"]

        screen.query_one("#add-custom-label", Button).press()
        await pilot.pause()
        screen.query_one("#custom-label-0-key", Input).value = "production"
        screen.query_one("#custom-label-0-value", Input).value = "true"
        screen.write_to_config(config)
        assert config.cluster.custom_labels == {"production": "true"}

        screen.query_one("#custom-label-0-remove", Button).press()
        await pilot.pause()
        assert not screen.query("#custom-label-0-key")
        screen.write_to_config(config)
        assert config.cluster.custom_labels == {}


@pytest.mark.asyncio
async def test_cluster_custom_label_row_fits_within_container() -> None:
    config = NVConfigManagerInstallConfig(
        cluster=ClusterConfig(custom_labels={"production": "true"})
    )
    app = NVConfigManagerInstallerApp(config=config)

    async with app.run_test(size=(100, 70)) as pilot:
        await pilot.pause()
        screen = app._screens["cluster"]
        labels_list = screen.query_one("#custom-labels-list")
        remove_button = screen.query_one("#custom-label-0-remove", Button)

        assert remove_button.region.right <= labels_list.content_region.right


@pytest.mark.asyncio
async def test_invalid_custom_label_blocks_navigation() -> None:
    config = NVConfigManagerInstallConfig()
    app = NVConfigManagerInstallerApp(config=config)

    async with app.run_test() as pilot:
        screen = app._screens["cluster"]
        screen.query_one("#add-custom-label", Button).press()
        await pilot.pause()
        screen.query_one("#custom-label-0-key", Input).value = "invalid key"
        screen.query_one("#custom-label-0-value", Input).value = "true"

        app.switch_section("services")

        assert app.active_section == "cluster"
        assert config.cluster.custom_labels == {}


@pytest.mark.asyncio
async def test_duplicate_custom_label_blocks_save(tmp_path: Path) -> None:
    config = NVConfigManagerInstallConfig(
        cluster=ClusterConfig(custom_labels={"production": "true", "include_in_slo": "true"})
    )
    config_path = tmp_path / "nv-config-manager-install.yaml"
    app = NVConfigManagerInstallerApp(config=config, config_path=config_path)

    async with app.run_test() as pilot:
        screen = app._screens["cluster"]
        screen.query_one("#custom-label-1-key", Input).value = "production"
        await pilot.pause()

        app.action_save()

        assert not config_path.exists()
        assert config.cluster.custom_labels == {
            "production": "true",
            "include_in_slo": "true",
        }


@pytest.mark.asyncio
async def test_airgapped_collected_from_cluster():
    """Airgapped setting should round-trip through the cluster screen."""
    config = NVConfigManagerInstallConfig(cluster=ClusterConfig(airgapped=True))
    app = NVConfigManagerInstallerApp(config=config)
    async with app.run_test():
        app.collect_config()
        assert app.config.cluster.airgapped is True


@pytest.mark.parametrize(
    "options",
    [DeployOptions(build_images=True), DeployOptions(load_kind=True)],
)
@pytest.mark.asyncio
async def test_deployment_start_selects_local_image_source(
    monkeypatch: pytest.MonkeyPatch, options: DeployOptions
):
    """Deployment must use local images when building or loading them."""
    config = NVConfigManagerInstallConfig()
    app = NVConfigManagerInstallerApp(config=config)
    deployer = Mock()
    deployer.return_value.steps = []
    deployer.create_steps.return_value = []
    monkeypatch.setattr("nv_config_manager_installer.tui.screens.deploy.Deployer", deployer)
    monkeypatch.setattr(DeployScreen, "_collect_deploy_options", lambda self: options)
    monkeypatch.setattr(DeployScreen, "_run_deploy", lambda self: None)

    async with app.run_test():
        screen = app._screens["deploy"]
        assert isinstance(screen, DeployScreen)
        screen._start_deploy()

    deployed_config, deployed_options, _ = deployer.call_args.args

    assert deployed_config.images.source == ImageSource.LOCAL
    assert deployed_options is options


@pytest.mark.asyncio
async def test_derived_installer_can_add_screen_and_select_deployer() -> None:
    class ProviderConfig(NVConfigManagerInstallConfig):
        provider_setting: str = "netbox-value"

    class ProviderDeployer(Deployer):
        CONFIG_MODEL = ProviderConfig

        @classmethod
        def create_steps(cls) -> list[DeployStep]:
            return [DeployStep("provider", "Configure external provider")]

    class ProviderDeployScreen(DeployScreen):
        def deployer_class(self) -> type[Deployer]:
            return ProviderDeployer

    class ProviderScreen(Container):
        def __init__(self, config: NVConfigManagerInstallConfig, **kwargs: object) -> None:
            super().__init__(**kwargs)
            self._config = config

        def compose(self) -> ComposeResult:
            yield Label("Provider configuration")

        def get_status(self, config: NVConfigManagerInstallConfig) -> str:
            return "[*]"

    class ProviderInstallerApp(NVConfigManagerInstallerApp):
        CONFIG_MODEL = ProviderConfig
        SECTION_LABELS = (
            *NVConfigManagerInstallerApp.SECTION_LABELS[:-1],
            ("provider", "Provider"),
            NVConfigManagerInstallerApp.SECTION_LABELS[-1],
        )
        SCREEN_CLASSES = {
            **NVConfigManagerInstallerApp.SCREEN_CLASSES,
            "provider": ProviderScreen,
            "deploy": ProviderDeployScreen,
        }

    config = ProviderConfig()
    app = ProviderInstallerApp(config=config)
    async with app.run_test():
        app.switch_section("provider")
        assert isinstance(app._screens["provider"], ProviderScreen)

        deploy_screen = app._screens["deploy"]
        assert isinstance(deploy_screen, ProviderDeployScreen)
        assert deploy_screen._get_initial_steps()[0].id == "provider"

        deployer = deploy_screen.create_deployer(config, DeployOptions(), Mock())
        assert isinstance(deployer, ProviderDeployer)
        assert isinstance(deployer.config, ProviderConfig)
        assert deployer.config.provider_setting == "netbox-value"


@pytest.mark.asyncio
async def test_app_secrets_eso_defaults_site_path_and_collects_manual_value():
    config = NVConfigManagerInstallConfig(
        secrets=SecretsConfig(
            method=SecretsMethod.ESO,
            vault=VaultConfig(
                server="https://openbao.example.com",
                secrets_path="nv-config-manager",
                auth=VaultAuth(method=VaultAuthMethod.TOKEN, token_secret_name=""),
            ),
        ),
        sites=[SiteConfig(name="dc01")],
    )
    app = NVConfigManagerInstallerApp(config=config)

    async with app.run_test():
        app.switch_section("secrets")
        secrets_screen = app._screens["secrets"]

        assert secrets_screen.get_status(config) == "[*]"
        assert secrets_screen.query_one("#k8s-secrets-section").display is False
        secrets_screen.query_one("#vp-key-redis-password", Input).value = "redis_password"
        secrets_screen.query_one("#vp-value-redis-password", Input).value = "manual-password"
        app.collect_config()

        assert app.config.sites[0].vault_path == ""
        assert app.config.secrets.vault.paths.redis.keys["password"] == "redis_password"
        assert app.config.secrets.k8s.redis.values["password"] == "manual-password"


@pytest.mark.asyncio
async def test_network_secrets_eso_preserves_vault_sourced_entries():
    config = NVConfigManagerInstallConfig(
        secrets=SecretsConfig(method=SecretsMethod.ESO),
        network_secrets=[
            NetworkSecretEntry(
                name="RADIUS Password",
                secret_key="radius_password",
                source=PasswordSource.VAULT,
            )
        ],
    )
    app = NVConfigManagerInstallerApp(config=config)

    async with app.run_test() as pilot:
        app.switch_section("network_secrets")
        await pilot.pause()
        network_screen = app._screens["network_secrets"]
        assert network_screen.query_one("#ns-content").display is True

        app.collect_config()

        assert len(app.config.network_secrets) == 1
        assert app.config.network_secrets[0].source == PasswordSource.VAULT


@pytest.mark.asyncio
async def test_network_secrets_eso_initializes_required_site_entries():
    config = NVConfigManagerInstallConfig(secrets=SecretsConfig(method=SecretsMethod.ESO))
    app = NVConfigManagerInstallerApp(config=config)

    async with app.run_test() as pilot:
        app.switch_section("network_secrets")
        await pilot.pause()
        app.collect_config()

        secret_keys = {entry.secret_key for entry in app.config.network_secrets}
        assert {"root_password", "api_user_key"} <= secret_keys


def test_network_secrets_status_requires_manual_values():
    config = NVConfigManagerInstallConfig(
        network_secrets=[
            NetworkSecretEntry(
                name="BGP Password",
                secret_key="bgp_password",
                source=PasswordSource.MANUAL,
            )
        ]
    )
    screen = NetworkSecretsScreen(config)

    assert screen.get_status(config) == "[!]"

    config.network_secrets[0].value = "   "
    assert screen.get_status(config) == "[!]"

    config.network_secrets[0].value = "manual-password"
    assert screen.get_status(config) == "[*]"


@pytest.mark.asyncio
@patch("nv_config_manager_installer.tui.screens.vault.OpenBaoClient")
@patch("nv_config_manager_installer.tui.screens.vault.K8sClient")
async def test_app_secrets_marks_existing_vault_values_without_copying_them(
    mock_k8s_cls,
    mock_vault_cls,
):
    mock_k8s_cls.return_value.read_secret_data.return_value = {"token": "vault-token"}
    mock_vault_cls.return_value.read_secret.return_value = ({"password": "vault-secret"}, 1)
    config = NVConfigManagerInstallConfig(
        secrets=SecretsConfig(
            method=SecretsMethod.ESO,
            vault=VaultConfig(
                server="https://vault.example.com",
                secrets_path="nv-config-manager",
                auth=VaultAuth(
                    method=VaultAuthMethod.TOKEN,
                    token_secret_name="vault-token",
                ),
            ),
        ),
    )
    app = NVConfigManagerInstallerApp(config=config)

    async def run_inline(function):
        return function()

    with patch("nv_config_manager_installer.tui.screens.vault.asyncio.to_thread", new=run_inline):
        async with app.run_test():
            app.switch_section("secrets")
            await app.workers.wait_for_complete()
            secrets_screen = app._screens["secrets"]
            value_input = secrets_screen.query_one("#vp-value-redis-password", Input)

            assert value_input.password is True
            assert value_input.value
            assert value_input.value != "vault-secret"
            assert "Present in Vault" in str(
                secrets_screen.query_one("#vp-status-redis-password").render()
            )
            app.collect_config()
            assert "password" not in app.config.secrets.k8s.redis.values


@pytest.mark.asyncio
@patch("nv_config_manager_installer.tui.screens.vault.OpenBaoClient")
async def test_app_secrets_merges_partial_vault_key_overrides(mock_vault_cls, monkeypatch):
    monkeypatch.setenv("VAULT_TOKEN", "vault-token")
    defaults = VaultPathsConfig().nautobot.keys
    mock_vault_cls.return_value.read_secret.return_value = (
        {
            "api_token": "existing-token",
            **{vault_key: "existing" for key, vault_key in defaults.items() if key != "token"},
        },
        1,
    )
    config = NVConfigManagerInstallConfig(
        secrets=SecretsConfig(
            method=SecretsMethod.ESO,
            vault=VaultConfig(),
        ),
    )
    config.secrets.vault.paths.nautobot = VaultPathConfig(keys={"token": "api_token"})
    app = NVConfigManagerInstallerApp(config=config)

    async with app.run_test():
        app.switch_section("secrets")
        secrets_screen = app._screens["secrets"]
        config.secrets.vault.server = "https://vault.example.com"
        config.secrets.vault.secrets_path = "nv-config-manager"
        presence, error = secrets_screen._read_vault_presence()

        assert error is None
        assert presence is not None
        secrets_screen._apply_vault_presence(presence)
        for logical_name, default_key in defaults.items():
            key_input = secrets_screen.query_one(f"#vp-key-nautobot-{logical_name}", Input)
            assert key_input.value == ("api_token" if logical_name == "token" else default_key)
            assert "Present in Vault" in str(
                secrets_screen.query_one(f"#vp-status-nautobot-{logical_name}").render()
            )

        app.collect_config()
        assert app.config.secrets.vault.paths.nautobot.keys == {
            **defaults,
            "token": "api_token",
        }


@pytest.mark.asyncio
async def test_app_secrets_jwt_presence_guidance_requires_environment_token(monkeypatch):
    monkeypatch.delenv("VAULT_TOKEN", raising=False)
    monkeypatch.delenv("OPENBAO_TOKEN", raising=False)
    config = NVConfigManagerInstallConfig(
        secrets=SecretsConfig(
            method=SecretsMethod.ESO,
            vault=VaultConfig(
                server="https://vault.example.com",
                secrets_path="nv-config-manager",
                auth=VaultAuth(method=VaultAuthMethod.JWT),
            ),
        ),
    )
    app = NVConfigManagerInstallerApp(config=config)

    async with app.run_test():
        app.switch_section("secrets")
        await app.workers.wait_for_complete()
        status = app._screens["secrets"].query_one("#vault-presence-status")
        assert "JWT presence checks require VAULT_TOKEN or OPENBAO_TOKEN" in str(status.render())
