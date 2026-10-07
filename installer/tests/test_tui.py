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

from unittest.mock import Mock, patch

import pytest
from pydantic import BaseModel, ConfigDict
from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Input, Label, RadioButton, Select

from nv_config_manager_installer.deployer import Deployer, DeployOptions
from nv_config_manager_installer.providers.api import (
    DCIMInstallerProvider,
    DeployStepsContribution,
    InstallerProviderMetadata,
    TUIContribution,
)
from nv_config_manager_installer.providers.nautobot import NautobotInstallerProvider
from nv_config_manager_installer.schema import (
    BUILT_IN_NAUTOBOT_PROVIDER,
    ClusterConfig,
    DCIMConfig,
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
async def test_external_services_separates_dcim_and_nats() -> None:
    config = NVConfigManagerInstallConfig()
    app = NVConfigManagerInstallerApp(config=config)

    async with app.run_test():
        app.switch_section("external_services")
        screen = app._screens["external_services"]

        assert screen.query_one("#ext-dcim-provider", Select).value == BUILT_IN_NAUTOBOT_PROVIDER
        screen.query_one("#ext-dcim-local", LabeledSwitch).value = True
        screen.query_one("#ext-nats-enabled", LabeledSwitch).value = True
        screen.query_one("#ext-nats-server", Input).value = "nats://nats.example.com:4222"
        screen.query_one("#ext-nats-auth-method").value = NATSAuthMethod.JWT.value
        screen.write_to_config(config)

        assert config.dcim.provider == BUILT_IN_NAUTOBOT_PROVIDER
        assert config.dcim.local is True
        assert config.external_services.nats.enabled is True
        assert config.external_services.nats.server == "nats://nats.example.com:4222"
        assert config.external_services.nats.auth_method == NATSAuthMethod.JWT


@pytest.mark.asyncio
async def test_external_services_keeps_external_provider_on_save() -> None:
    config = NVConfigManagerInstallConfig(
        dcim=DCIMConfig(provider="synthetic", local=False, server="https://synthetic.example"),
    )
    app = NVConfigManagerInstallerApp(config=config)

    async with app.run_test():
        app.switch_section("external_services")
        screen = app._screens["external_services"]
        assert screen.query_one("#ext-dcim-provider", Select).value == "synthetic"
        assert screen.query_one("#ext-dcim-local", LabeledSwitch).disabled is True

        screen.write_to_config(config)

        assert config.dcim.provider == "synthetic"
        assert config.dcim.local is False
        assert config.dcim.server == "https://synthetic.example"
        assert "ingest_data" not in app._screens


@pytest.mark.asyncio
async def test_switching_provider_rebuilds_provider_sections() -> None:
    config = NVConfigManagerInstallConfig(dcim=DCIMConfig(settings={"jobs": [{"path": "/j"}]}))
    app = NVConfigManagerInstallerApp(config=config)

    async with app.run_test() as pilot:
        assert "ingest_data" in app._screens
        app.switch_section("external_services")
        screen = app._screens["external_services"]

        screen.query_one("#ext-dcim-provider", Select).value = "synthetic"
        await pilot.pause(0.1)

        assert app.provider is not None and app.provider.metadata.name == "synthetic"
        assert "ingest_data" not in app._screens
        assert "ingest_data" not in [sid for sid, _ in app.section_labels()]
        assert config.dcim.provider == "synthetic"
        assert config.dcim.settings == {}

        screen.query_one("#ext-dcim-provider", Select).value = BUILT_IN_NAUTOBOT_PROVIDER
        await pilot.pause(0.1)

        assert "ingest_data" in app._screens
        labels = [sid for sid, _ in app.section_labels()]
        assert labels.index("ingest_data") == labels.index("network_secrets") + 1


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
async def test_installed_provider_contributes_screen_and_deploy_steps() -> None:
    class ProviderSettings(BaseModel):
        model_config = ConfigDict(extra="forbid")
        provider_setting: str = "netbox-value"

    class ProviderScreen(Container):
        def __init__(self, config: NVConfigManagerInstallConfig, **kwargs: object) -> None:
            super().__init__(**kwargs)
            self._config = config

        def compose(self) -> ComposeResult:
            yield Label("Provider configuration")

        def get_status(self, config: NVConfigManagerInstallConfig) -> str:
            return "[*]"

    class Provider(DCIMInstallerProvider):
        metadata = InstallerProviderMetadata(name="external-dcim", display_name="External DCIM")
        settings_model = ProviderSettings
        tui = TUIContribution(
            sections=(("provider", "Provider", ProviderScreen),), insert_after="infrastructure"
        )

        def deploy_steps(self) -> DeployStepsContribution:
            return DeployStepsContribution(
                before={"helm-install": (("provider", "Configure external provider"),)}
            )

    provider = Provider()
    config = NVConfigManagerInstallConfig(
        dcim=DCIMConfig(provider="external-dcim", local=False, server="https://dcim.example"),
    )
    app = NVConfigManagerInstallerApp(config=config, provider=provider)
    async with app.run_test():
        app.switch_section("provider")
        assert isinstance(app._screens["provider"], ProviderScreen)
        labels = [sid for sid, _ in app.section_labels()]
        assert labels.index("provider") == labels.index("infrastructure") + 1

        deploy_screen = app._screens["deploy"]
        assert isinstance(deploy_screen, DeployScreen)
        step_ids = [step.id for step in deploy_screen._get_initial_steps()]
        assert step_ids.index("provider") == step_ids.index("helm-install") - 1

        deployer = deploy_screen.create_deployer(config, DeployOptions(), Mock())
        assert isinstance(deployer, Deployer)
        assert deployer.provider is provider
        assert provider.settings(deployer.config).provider_setting == "netbox-value"


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
    defaults = NautobotInstallerProvider.secrets.groups[0].keys
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
