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
"""Tests for conditional field visibility in TUI screens."""

from __future__ import annotations

import pytest
from textual.widgets import Input

from nv_config_manager_installer.schema import (
    ExternalServicesConfig,
    ExternalTemporalConfig,
    NVConfigManagerInstallConfig,
    SecretsMethod,
    SiteConfig,
    ZTPStorageType,
)
from nv_config_manager_installer.tui.app import NVConfigManagerInstallerApp


def _assert_displayed(widget: object) -> None:
    node = widget
    while node is not None:
        display = getattr(node, "display", True)
        assert display is True
        node = getattr(node, "parent", None)


@pytest.mark.asyncio
async def test_vault_fields_hidden_when_kubernetes():
    """Vault fields should be hidden when secrets method is kubernetes."""
    config = NVConfigManagerInstallConfig()
    config.secrets.method = SecretsMethod.KUBERNETES
    app = NVConfigManagerInstallerApp(config=config)
    async with app.run_test():
        app.switch_section("secrets")
        vault_fields = app.query_one("#vault-fields")
        assert vault_fields.display is False


@pytest.mark.asyncio
async def test_vault_fields_shown_when_eso():
    """Vault fields should be visible when secrets method is ESO."""
    config = NVConfigManagerInstallConfig()
    config.secrets.method = SecretsMethod.ESO
    app = NVConfigManagerInstallerApp(config=config)
    async with app.run_test():
        app.switch_section("secrets")
        vault_fields = app.query_one("#vault-fields")
        assert vault_fields.display is True
        assert app.query_one("#k8s-secrets-section").display is False
        assert app.query_one("#vp-value-redis-password", Input) is not None


@pytest.mark.asyncio
async def test_sso_fields_hidden_when_disabled():
    """SSO fields should be hidden when SSO is disabled."""
    config = NVConfigManagerInstallConfig()
    config.sso.enabled = False
    app = NVConfigManagerInstallerApp(config=config)
    async with app.run_test():
        app.switch_section("sso")
        sso_fields = app.query_one("#sso-fields")
        assert sso_fields.display is False


@pytest.mark.asyncio
async def test_sso_fields_shown_when_enabled():
    """SSO fields should be visible when SSO is enabled."""
    config = NVConfigManagerInstallConfig()
    config.sso.enabled = True
    app = NVConfigManagerInstallerApp(config=config)
    async with app.run_test():
        app.switch_section("sso")
        sso_fields = app.query_one("#sso-fields")
        assert sso_fields.display is True


@pytest.mark.asyncio
async def test_spiffe_fields_hidden_when_disabled():
    """SPIFFE fields should be hidden when SPIFFE is disabled."""
    config = NVConfigManagerInstallConfig()
    config.spiffe.enabled = False
    app = NVConfigManagerInstallerApp(config=config)
    async with app.run_test():
        app.switch_section("spiffe")
        spiffe_fields = app.query_one("#spiffe-fields")
        assert spiffe_fields.display is False


@pytest.mark.asyncio
async def test_cnpg_fields_hidden_when_backup_disabled():
    """CNPG backup fields should be hidden when backup is disabled."""
    config = NVConfigManagerInstallConfig()
    config.infrastructure.cnpg_s3_backup.enabled = False
    app = NVConfigManagerInstallerApp(config=config)
    async with app.run_test():
        app.switch_section("infrastructure")
        cnpg_fields = app.query_one("#cnpg-backup-fields")
        assert cnpg_fields.display is False


@pytest.mark.asyncio
async def test_deploy_section_exists():
    """Deploy section should exist in the nav."""
    config = NVConfigManagerInstallConfig()
    app = NVConfigManagerInstallerApp(config=config)
    async with app.run_test():
        assert "deploy" in app._nav_items
        assert "deploy" in app._screens


@pytest.mark.asyncio
async def test_temporal_server_sizing_fields_shown_for_managed_temporal():
    app = NVConfigManagerInstallerApp(config=NVConfigManagerInstallConfig())

    async with app.run_test():
        app.switch_section("workflows")

        assert app.query_one("#temporal-server-fields").display is True


@pytest.mark.asyncio
async def test_temporal_server_sizing_fields_hidden_for_external_temporal():
    config = NVConfigManagerInstallConfig(
        external_services=ExternalServicesConfig(
            temporal=ExternalTemporalConfig(address="temporal.example.com:7233")
        )
    )
    app = NVConfigManagerInstallerApp(config=config)

    async with app.run_test():
        app.switch_section("workflows")

        assert app.query_one("#temporal-server-fields").display is False


@pytest.mark.asyncio
async def test_temporal_server_sizing_fields_hidden_when_temporal_disabled():
    config = NVConfigManagerInstallConfig()
    config.services.temporal = False
    app = NVConfigManagerInstallerApp(config=config)

    async with app.run_test():
        app.switch_section("workflows")

        assert app.query_one("#temporal-server-fields").display is False


@pytest.mark.asyncio
async def test_site_vault_paths_shown_in_eso_mode():
    """Site vault path inputs should appear in App Secrets when ESO is active."""
    config = NVConfigManagerInstallConfig()
    config.secrets.method = SecretsMethod.ESO
    config.sites = [SiteConfig(name="dc01"), SiteConfig(name="dc02")]
    app = NVConfigManagerInstallerApp(config=config)
    async with app.run_test() as pilot:
        app.switch_section("secrets")
        await pilot.pause(0.1)
        secrets_screen = app._screens["secrets"]
        assert secrets_screen.query_one("#site-vault-0", Input) is not None
        assert secrets_screen.query_one("#site-vault-1", Input) is not None


@pytest.mark.asyncio
async def test_site_vault_paths_reflect_cluster_sites():
    """Navigating Cluster → App Secrets should populate vault path rows from config.sites."""
    config = NVConfigManagerInstallConfig()
    config.secrets.method = SecretsMethod.ESO
    config.sites = [SiteConfig(name="dc01", vault_path="prod/site/dc01/cfg")]
    app = NVConfigManagerInstallerApp(config=config)
    async with app.run_test() as pilot:
        app.switch_section("cluster")
        app.switch_section("secrets")
        await pilot.pause(0.1)
        secrets_screen = app._screens["secrets"]
        inp = secrets_screen.query_one("#site-vault-0", Input)
        assert inp.value == "prod/site/dc01/cfg"


@pytest.mark.asyncio
async def test_ztp_s3_app_secret_fields_shown_in_kubernetes_mode():
    """ZTP S3 app secret inputs should be available in Kubernetes secret mode."""
    config = NVConfigManagerInstallConfig()
    config.secrets.method = SecretsMethod.KUBERNETES
    config.secrets.k8s.ztp_s3.enabled = True
    app = NVConfigManagerInstallerApp(config=config)
    async with app.run_test() as pilot:
        app.switch_section("secrets")
        await pilot.pause(0.1)
        secrets_screen = app._screens["secrets"]
        card = secrets_screen.query_one("#k8s-card-ztp_s3")
        fields = secrets_screen.query_one("#k8s-fields-ztp_s3")
        endpoint = secrets_screen.query_one("#k8s-ztp_s3-endpoint", Input)
        access_key = secrets_screen.query_one("#k8s-ztp_s3-accessKeyId", Input)
        secret_key = secrets_screen.query_one("#k8s-ztp_s3-secretAccessKey", Input)
        for widget in (card, fields, endpoint, access_key, secret_key):
            _assert_displayed(widget)


@pytest.mark.asyncio
async def test_ztp_s3_app_secret_fields_shown_in_eso_mode():
    """ZTP S3 Vault path inputs should be available in ESO secret mode."""
    config = NVConfigManagerInstallConfig()
    config.secrets.method = SecretsMethod.ESO
    app = NVConfigManagerInstallerApp(config=config)
    async with app.run_test() as pilot:
        app.switch_section("secrets")
        await pilot.pause(0.1)
        secrets_screen = app._screens["secrets"]
        card = secrets_screen.query_one("#vp-card-ztp_s3")
        path = secrets_screen.query_one("#vp-path-ztp_s3", Input)
        endpoint = secrets_screen.query_one("#vp-key-ztp_s3-endpoint", Input)
        access_key = secrets_screen.query_one("#vp-key-ztp_s3-accessKeyId", Input)
        secret_key = secrets_screen.query_one("#vp-key-ztp_s3-secretAccessKey", Input)
        for widget in (card, path, endpoint, access_key, secret_key):
            _assert_displayed(widget)


@pytest.mark.asyncio
async def test_ztp_ceph_keeps_bucket_input_visible():
    """Ceph uses the S3 bucket setting but hides the custom endpoint."""
    config = NVConfigManagerInstallConfig()
    ztp_storage = config.infrastructure.ztp_storage
    ztp_storage.type = ZTPStorageType.S3
    ztp_storage.s3_bucket = "firmware-images"
    ztp_storage.s3_endpoint = "https://ignored.example"
    ztp_storage.s3_region = "us-west-2"
    ztp_storage.s3_ceph.enabled = True

    app = NVConfigManagerInstallerApp(config=config)
    async with app.run_test() as pilot:
        app.switch_section("ztp")
        await pilot.pause(0.1)
        ztp_screen = app._screens["ztp"]
        assert ztp_screen.query_one("#ztp-s3-bucket-fields").display is True
        assert ztp_screen.query_one("#ztp-s3-endpoint-fields").display is False
        assert ztp_screen.query_one("#ztp-s3-region-fields").display is False
        assert ztp_screen.query_one("#ztp-s3-bucket", Input).value == "firmware-images"
        app.collect_config()

    assert config.infrastructure.ztp_storage.s3_bucket == "firmware-images"
    assert config.infrastructure.ztp_storage.s3_endpoint == ""
    assert config.infrastructure.ztp_storage.s3_region == ""
