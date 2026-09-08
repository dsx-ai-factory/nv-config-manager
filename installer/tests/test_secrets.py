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
"""Tests for nv_config_manager_installer.secrets -- secret generation and ESO config building."""

from __future__ import annotations

import json

import pytest

from nv_config_manager_installer import secrets as secrets_module
from nv_config_manager_installer.schema import (
    DCIMConfig,
    InfrastructureConfig,
    K8sSecretGroup,
    KubernetesSecretsConfig,
    NetworkSecretEntry,
    NVConfigManagerInstallConfig,
    PasswordSource,
    RedfishConfig,
    RedfishVendorCreds,
    SecretsConfig,
    SecretsMethod,
    ServicesConfig,
    SSOConfig,
    VaultAuth,
    VaultAuthMethod,
    VaultConfig,
    VaultPathConfig,
    VaultPathsConfig,
    ZTPStorageConfig,
    ZTPStorageType,
)
from nv_config_manager_installer.secrets import (
    build_eso_vault_config,
    build_openbao_secret_data,
    generate_secrets,
)


class TestGenerateSecrets:
    def test_network_secrets_generated(self):
        config = NVConfigManagerInstallConfig(
            network_secrets=[
                NetworkSecretEntry(name="BGP Password", secret_key="bgp_password"),
                NetworkSecretEntry(name="ISIS Password", secret_key="isis_password"),
                NetworkSecretEntry(name="TACACS Key", secret_key="tacacs_key"),
            ]
        )
        state = generate_secrets(config)

        assert "bgp_password_r1" in state
        assert "isis_password_r1" in state
        assert "tacacs_key_r1" in state
        assert "hash_salt" in state

    def test_infra_secrets_for_kubernetes_method(self):
        config = NVConfigManagerInstallConfig(
            secrets=SecretsConfig(method=SecretsMethod.KUBERNETES)
        )
        state = generate_secrets(config)

        assert "nautobot_token" in state
        assert len(state["nautobot_token"]) == 40
        assert "nautobot_read_only_token" not in state
        assert "redis_password" in state
        assert "nats_password" in state
        assert "django_secret_key" in state
        assert "temporal_db_password" in state

    def test_external_dcim_token_uses_generic_secret_group(self):
        config = NVConfigManagerInstallConfig(
            dcim=DCIMConfig(provider="synthetic", server="https://synthetic.example"),
            services=ServicesConfig(nautobot=False),
            secrets=SecretsConfig(
                method=SecretsMethod.KUBERNETES,
                k8s=KubernetesSecretsConfig(
                    dcim=K8sSecretGroup(values={"token": "synthetic-token"}),
                ),
            ),
        )

        state = generate_secrets(config)

        assert state["dcim_token"] == "synthetic-token"
        assert "nautobot_token" not in state
        assert "nautobot_admin_password" not in state

    def test_external_dcim_uses_independent_nats_password(self):
        config = NVConfigManagerInstallConfig(
            dcim=DCIMConfig(provider="synthetic", server="https://synthetic.example"),
            services=ServicesConfig(nautobot=False),
            secrets=SecretsConfig(
                method=SecretsMethod.KUBERNETES,
                k8s=KubernetesSecretsConfig(
                    nats=K8sSecretGroup(values={"password": "NatsPassword123456"}),
                    nautobot=K8sSecretGroup(values={"natsPassword": "LegacyPassword1234"}),
                ),
            ),
        )

        state = generate_secrets(config)

        assert state["nats_password"] == "NatsPassword123456"

    def test_external_dcim_accepts_legacy_nats_password_fallback(self):
        config = NVConfigManagerInstallConfig(
            dcim=DCIMConfig(provider="synthetic", server="https://synthetic.example"),
            services=ServicesConfig(nautobot=False),
            secrets=SecretsConfig(
                method=SecretsMethod.KUBERNETES,
                k8s=KubernetesSecretsConfig(
                    nautobot=K8sSecretGroup(values={"natsPassword": "LegacyPassword1234"}),
                ),
            ),
        )

        assert generate_secrets(config)["nats_password"] == "LegacyPassword1234"

    def test_generated_nats_password_is_safe_for_unquoted_nats_config_variable(self, monkeypatch):
        monkeypatch.setattr(secrets_module.secrets, "choice", lambda alphabet: alphabet[-1])

        assert secrets_module._generate_nats_config_password(16) == "Z" + ("9" * 15)

    def test_generated_nats_password_rejects_too_short_lengths(self):
        with pytest.raises(ValueError, match="length must be at least 16"):
            secrets_module._generate_nats_config_password(2)

    def test_kubernetes_nats_password_override_must_be_config_safe(self):
        config = NVConfigManagerInstallConfig(
            secrets=SecretsConfig(
                method=SecretsMethod.KUBERNETES,
                k8s=KubernetesSecretsConfig(
                    nautobot=K8sSecretGroup(
                        values={"natsPassword": "2026-06-25aaaaaaaaaaaaaaaaaaaaaa"}
                    ),
                ),
            ),
        )

        with pytest.raises(ValueError, match=r"natsPassword must match"):
            generate_secrets(config)

    def test_kubernetes_nautobot_read_only_token_passes_through(self):
        config = NVConfigManagerInstallConfig(
            secrets=SecretsConfig(
                method=SecretsMethod.KUBERNETES,
                k8s=KubernetesSecretsConfig(
                    nautobot=K8sSecretGroup(values={"readOnlyToken": "ro-token"}),
                ),
            ),
        )
        state = generate_secrets(config)

        assert state["nautobot_read_only_token"] == "ro-token"

    def test_kubernetes_nautobot_admin_password_override(self):
        config = NVConfigManagerInstallConfig(
            secrets=SecretsConfig(
                method=SecretsMethod.KUBERNETES,
                k8s=KubernetesSecretsConfig(
                    nautobot_app=K8sSecretGroup(values={"adminPassword": "admin"}),
                ),
            ),
        )
        state = generate_secrets(config)

        assert state["nautobot_admin_password"] == "admin"

    def test_kubernetes_ztp_s3_credentials_pass_through(self):
        config = NVConfigManagerInstallConfig(
            secrets=SecretsConfig(
                method=SecretsMethod.KUBERNETES,
                k8s=KubernetesSecretsConfig(
                    ztp_s3=K8sSecretGroup(
                        enabled=True,
                        values={
                            "endpoint": "https://minio.example",
                            "accessKeyId": "access",
                            "secretAccessKey": "secret",
                        },
                    )
                ),
            ),
            infrastructure=InfrastructureConfig(
                ztp_storage=ZTPStorageConfig(type=ZTPStorageType.S3)
            ),
        )
        state = generate_secrets(config)

        assert state["ztp_s3_endpoint"] == "https://minio.example"
        assert state["ztp_s3_access_key_id"] == "access"
        assert state["ztp_s3_secret_access_key"] == "secret"

    def test_kubernetes_ztp_s3_credentials_optional(self):
        config = NVConfigManagerInstallConfig(
            secrets=SecretsConfig(method=SecretsMethod.KUBERNETES),
            infrastructure=InfrastructureConfig(
                ztp_storage=ZTPStorageConfig(type=ZTPStorageType.S3)
            ),
        )
        state = generate_secrets(config)

        assert "ztp_s3_access_key_id" not in state
        assert "ztp_s3_secret_access_key" not in state

    def test_infra_secrets_omitted_for_eso(self):
        config = NVConfigManagerInstallConfig(secrets=SecretsConfig(method=SecretsMethod.ESO))
        state = generate_secrets(config)

        assert "nautobot_token" not in state
        assert "redis_password" not in state

    def test_eso_generates_required_site_secrets_without_tui_defaults(self):
        config = NVConfigManagerInstallConfig(secrets=SecretsConfig(method=SecretsMethod.ESO))

        state = generate_secrets(config)

        assert state["root_password_r1"]
        assert state["api_user_key_r1"]

    def test_eso_does_not_generate_over_vault_sourced_required_site_secret(self):
        config = NVConfigManagerInstallConfig(
            secrets=SecretsConfig(method=SecretsMethod.ESO),
            network_secrets=[
                NetworkSecretEntry(
                    name="Device Admin Password",
                    secret_key="root_password",
                    source=PasswordSource.VAULT,
                )
            ],
        )

        state = generate_secrets(config)

        assert "root_password_r1" not in state
        assert state["api_user_key_r1"]

    def test_unique_network_secret_passwords(self):
        config = NVConfigManagerInstallConfig(
            network_secrets=[
                NetworkSecretEntry(
                    name="Key 1",
                    secret_key="k1",
                    source=PasswordSource.GENERATE,
                ),
                NetworkSecretEntry(
                    name="Key 2",
                    secret_key="k2",
                    source=PasswordSource.GENERATE,
                ),
            ],
        )
        state = generate_secrets(config)
        assert state["k1_r1"] != state["k2_r1"]

    def test_manual_network_secret_raises_when_no_value(self):
        config = NVConfigManagerInstallConfig(
            network_secrets=[
                NetworkSecretEntry(
                    name="Manual Key",
                    secret_key="manual_pw",
                    source=PasswordSource.MANUAL,
                ),
            ],
        )
        with pytest.raises(ValueError, match="manual_pw_r1"):
            generate_secrets(config)

    def test_manual_network_secret_uses_supplied_value(self):
        config = NVConfigManagerInstallConfig(
            network_secrets=[
                NetworkSecretEntry(
                    name="Manual Key",
                    secret_key="manual_pw",
                    source=PasswordSource.MANUAL,
                    value="mysecret",
                ),
            ],
        )
        state = generate_secrets(config)
        assert state["manual_pw_r1"] == "mysecret"

    def test_non_rotated_network_secret_uses_bare_key(self):
        config = NVConfigManagerInstallConfig(
            network_secrets=[
                NetworkSecretEntry(
                    name="UFM API User",
                    secret_key="ufm_api_user",
                    source=PasswordSource.MANUAL,
                    rotation="",
                    value="admin",
                ),
            ],
        )
        state = generate_secrets(config)

        assert state["ufm_api_user"] == "admin"
        assert "ufm_api_user_" not in state

    def test_vault_network_secret_omitted(self):
        config = NVConfigManagerInstallConfig(
            network_secrets=[
                NetworkSecretEntry(
                    name="Vault Key",
                    secret_key="vault_pw",
                    source=PasswordSource.VAULT,
                ),
            ],
        )
        state = generate_secrets(config)
        assert "vault_pw_r1" not in state


class TestESOVaultConfig:
    def test_returns_empty_for_kubernetes(self):
        config = NVConfigManagerInstallConfig(
            secrets=SecretsConfig(method=SecretsMethod.KUBERNETES)
        )
        assert build_eso_vault_config(config) == {}

    def test_jwt_auth_config(self):
        config = NVConfigManagerInstallConfig(
            secrets=SecretsConfig(
                method=SecretsMethod.ESO,
                vault=VaultConfig(
                    server="https://vault.test",
                    secrets_path="nv-config-manager",
                    mount_path="auth/kubernetes/prod",
                    role="test-role",
                    auth=VaultAuth(method=VaultAuthMethod.JWT),
                ),
            ),
        )
        config.cluster.environment = "prod"
        result = build_eso_vault_config(config)

        assert result["secrets"]["method"] == "eso"
        assert result["secrets"]["vault"]["server"] == "https://vault.test"
        assert result["secrets"]["vault"]["mountPath"] == "auth/kubernetes/prod"
        assert result["secrets"]["vault"]["role"] == "test-role"
        assert "jwtAuth" not in result["secrets"]["vault"]
        assert result["secrets"]["vault"]["paths"]["nautobot"]["path"] == "prod/nautobot"

    def test_token_auth_config(self):
        config = NVConfigManagerInstallConfig(
            secrets=SecretsConfig(
                method=SecretsMethod.ESO,
                vault=VaultConfig(
                    auth=VaultAuth(method=VaultAuthMethod.TOKEN, token_secret_name="my-token"),
                ),
            ),
        )
        result = build_eso_vault_config(config)

        assert "tokenAuth" in result["secrets"]["vault"]
        assert result["secrets"]["vault"]["tokenAuth"]["secretName"] == "my-token"
        assert "mountPath" not in result["secrets"]["vault"]
        assert "role" not in result["secrets"]["vault"]

    def test_all_default_path_groups(self):
        config = NVConfigManagerInstallConfig(
            secrets=SecretsConfig(
                method=SecretsMethod.ESO,
                vault=VaultConfig(server="https://vault.test"),
            ),
        )
        config.cluster.environment = "prod"
        result = build_eso_vault_config(config)
        paths = result["secrets"]["vault"]["paths"]

        # Core groups always enabled by default
        for group in (
            "nautobot",
            "redis",
            "postgres",
            "network",
            "nautobotApp",
            "oidc",
            "redfish",
            "bmc",
        ):
            assert group in paths, f"{group} missing from paths"
            assert "path" in paths[group]
            assert "keys" in paths[group]

        # Optional groups disabled by default
        for group in ("slack", "jira", "cnpgBackup", "ztpS3"):
            assert group not in paths, f"{group} should be disabled by default"

    def test_custom_path_preserves_default_keys(self):
        config = NVConfigManagerInstallConfig(
            secrets=SecretsConfig(
                method=SecretsMethod.ESO,
                vault=VaultConfig(
                    server="https://vault.test",
                    paths=VaultPathsConfig(
                        nautobot=VaultPathConfig(
                            path="groups/ngc-cfa/nv-config-manager/eks-test/nautobot"
                        ),
                    ),
                ),
            ),
        )
        result = build_eso_vault_config(config)
        nb = result["secrets"]["vault"]["paths"]["nautobot"]

        assert nb["path"] == "groups/ngc-cfa/nv-config-manager/eks-test/nautobot"
        assert nb["keys"]["token"] == "token"
        assert nb["keys"]["natsPassword"] == "nats_password"

    def test_custom_keys_override(self):
        config = NVConfigManagerInstallConfig(
            secrets=SecretsConfig(
                method=SecretsMethod.ESO,
                vault=VaultConfig(
                    server="https://vault.test",
                    paths=VaultPathsConfig(
                        nautobot=VaultPathConfig(
                            path="custom/nautobot",
                            keys={"token": "nb_token", "natsPassword": "nats_pw_dev"},
                        ),
                    ),
                ),
            ),
        )
        result = build_eso_vault_config(config)
        nb = result["secrets"]["vault"]["paths"]["nautobot"]

        assert nb["keys"]["token"] == "nb_token"
        assert nb["keys"]["natsPassword"] == "nats_pw_dev"

    def test_enabled_optional_group(self):
        config = NVConfigManagerInstallConfig(
            secrets=SecretsConfig(
                method=SecretsMethod.ESO,
                vault=VaultConfig(
                    server="https://vault.test",
                    paths=VaultPathsConfig(
                        slack=VaultPathConfig(enabled=True, path="prod/slack"),
                    ),
                ),
            ),
        )
        result = build_eso_vault_config(config)
        paths = result["secrets"]["vault"]["paths"]

        assert "slack" in paths
        assert paths["slack"]["path"] == "prod/slack"
        assert paths["slack"]["keys"]["token"] == "token"

    def test_disabled_core_group(self):
        config = NVConfigManagerInstallConfig(
            secrets=SecretsConfig(
                method=SecretsMethod.ESO,
                vault=VaultConfig(
                    server="https://vault.test",
                    paths=VaultPathsConfig(
                        redfish=VaultPathConfig(enabled=False),
                    ),
                ),
            ),
        )
        result = build_eso_vault_config(config)
        paths = result["secrets"]["vault"]["paths"]

        assert "redfish" not in paths

    def test_oidc_includes_client_and_cookie_secrets(self):
        config = NVConfigManagerInstallConfig(
            secrets=SecretsConfig(
                method=SecretsMethod.ESO,
                vault=VaultConfig(server="https://vault.test"),
            ),
        )
        result = build_eso_vault_config(config)
        oidc_keys = result["secrets"]["vault"]["paths"]["oidc"]["keys"]

        assert set(oidc_keys) == {"clientSecret", "cookieSecret"}


class TestOpenBaoSecretData:
    def test_builds_core_and_configured_integration_values(self):
        config = NVConfigManagerInstallConfig(
            secrets=SecretsConfig(
                method=SecretsMethod.ESO,
                k8s=KubernetesSecretsConfig(
                    network=K8sSecretGroup(
                        values={"user": "automation", "password": "device-password"}
                    ),
                ),
            ),
            sso=SSOConfig(enabled=True, client_secret="oidc-secret"),
            redfish=RedfishConfig(
                enabled=True,
                vendors={
                    "lenovo": RedfishVendorCreds(
                        default_user="USERID",
                        default_password="lenovo-password",
                    )
                },
            ),
        )

        groups = build_openbao_secret_data(config)

        assert groups["network"] == {
            "user": "automation",
            "password": "device-password",
        }
        assert groups["oidc"]["clientSecret"] == "oidc-secret"
        assert groups["redfish"]["lenovoDefaultUser"] == "USERID"
        assert groups["redfish"]["lenovoDefaultPassword"] == "lenovo-password"
        assert "credsJson" in groups["bmc"]
        assert len(groups["nautobot"]["token"]) == 40
        assert groups["postgres"]["temporalUser"] == "temporal"

    def test_includes_ztp_s3_credentials_when_eso_file_storage_is_enabled(self):
        config = NVConfigManagerInstallConfig(
            secrets=SecretsConfig(
                method=SecretsMethod.ESO,
                k8s=KubernetesSecretsConfig(
                    ztp_s3=K8sSecretGroup(
                        enabled=True,
                        values={
                            "endpoint": "https://minio.example",
                            "accessKeyId": "access",
                            "secretAccessKey": "secret",
                        },
                    )
                ),
                vault=VaultConfig(paths=VaultPathsConfig(ztp_s3=VaultPathConfig(enabled=True))),
            ),
            infrastructure=InfrastructureConfig(
                ztp_storage=ZTPStorageConfig(type=ZTPStorageType.S3)
            ),
        )

        groups = build_openbao_secret_data(config)

        assert groups["ztp_s3"] == {
            "endpoint": "https://minio.example",
            "accessKeyId": "access",
            "secretAccessKey": "secret",
        }

    def test_bmc_default_username_falls_back_when_configured_value_is_empty(self):
        config = NVConfigManagerInstallConfig(
            secrets=SecretsConfig(method=SecretsMethod.ESO),
            redfish=RedfishConfig(
                enabled=True,
                vendors={
                    "default": RedfishVendorCreds(
                        default_user="",
                        default_password="bmc-password",
                    )
                },
            ),
        )

        groups = build_openbao_secret_data(config)

        creds = json.loads(groups["bmc"]["credsJson"])
        assert creds["default"] == {
            "username": "admin",
            "password": "bmc-password",
        }


class TestRedfishSecrets:
    def test_no_redfish_secrets_when_disabled(self):
        config = NVConfigManagerInstallConfig(
            secrets=SecretsConfig(method=SecretsMethod.KUBERNETES),
            redfish=RedfishConfig(enabled=False),
        )
        state = generate_secrets(config)
        assert not any(k.startswith("redfish_") for k in state)

    def test_redfish_secrets_generated_when_enabled(self):
        config = NVConfigManagerInstallConfig(
            secrets=SecretsConfig(method=SecretsMethod.KUBERNETES),
            redfish=RedfishConfig(
                enabled=True,
                vendors={
                    "lenovo": RedfishVendorCreds(),
                    "bluefield": RedfishVendorCreds(),
                },
            ),
        )
        state = generate_secrets(config)
        assert "redfish_lenovo_default_user" in state
        assert state["redfish_lenovo_default_user"] == "local-mock-user"
        assert "redfish_lenovo_default_password" in state
        assert len(state["redfish_lenovo_default_password"]) == 32
        assert "redfish_lenovo_config_manager_password" in state
        assert "redfish_bluefield_default_user" in state
        assert "redfish_bluefield_default_password" in state
        assert "redfish_bluefield_config_manager_password" in state

    def test_redfish_user_supplied_values_preserved(self):
        config = NVConfigManagerInstallConfig(
            secrets=SecretsConfig(method=SecretsMethod.KUBERNETES),
            redfish=RedfishConfig(
                enabled=True,
                vendors={
                    "lenovo": RedfishVendorCreds(
                        default_user="myadmin",
                        default_password="mypass",
                        config_manager_password="kpass",
                    ),
                },
            ),
        )
        state = generate_secrets(config)
        assert state["redfish_lenovo_default_user"] == "myadmin"
        assert state["redfish_lenovo_default_password"] == "mypass"
        assert state["redfish_lenovo_config_manager_password"] == "kpass"

    def test_no_redfish_secrets_for_eso(self):
        config = NVConfigManagerInstallConfig(
            secrets=SecretsConfig(method=SecretsMethod.ESO),
            redfish=RedfishConfig(
                enabled=True,
                vendors={"lenovo": RedfishVendorCreds()},
            ),
        )
        state = generate_secrets(config)
        assert not any(k.startswith("redfish_") for k in state)
