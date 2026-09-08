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
"""Tests for nv_config_manager_installer.schema -- config validation and serialization."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from nv_config_manager_installer.schema import (
    ClusterConfig,
    CNPGBackupConfig,
    ContentConfig,
    DCIMConfig,
    DeploySize,
    ExternalPostgresConfig,
    ExternalRedisConfig,
    ExternalServicesConfig,
    ExternalTemporalConfig,
    GatewayType,
    GitTokenEntry,
    ImageOverride,
    ImagePullSecret,
    ImagesConfig,
    ImageSource,
    InfrastructureConfig,
    LBProvider,
    LoadBalancerConfig,
    MonitoringConfig,
    NetworkSecretEntry,
    NVConfigManagerInstallConfig,
    RedfishConfig,
    RedfishVendorCreds,
    SecretsConfig,
    SecretsMethod,
    ServicesConfig,
    SiteConfig,
    SPIFFEConfig,
    SPIFFEProvider,
    SSOConfig,
    SSOProvider,
    TemporalAuthMethod,
    VaultAuthMethod,
    ZTPOSImage,
    ZTPS3CephConfig,
    ZTPS3CephObjectBucketClaimConfig,
    ZTPStorageConfig,
    ZTPStorageType,
)


class TestNVConfigManagerInstallConfig:
    def test_default_config(self):
        config = NVConfigManagerInstallConfig()
        assert config.version == "1"
        assert config.cluster.environment == "local"
        assert config.secrets.method == SecretsMethod.KUBERNETES
        assert config.secrets.config_manager_service_username == "nv-config-manager"
        assert config.services.render is True
        assert config.dcim.provider == "nautobot-2x"
        assert config.infrastructure.monitoring.redis_metrics_enabled is False

    def test_external_temporal_mtls_requires_address_and_secret(self):
        with pytest.raises(ValueError, match="requires an address"):
            ExternalTemporalConfig(auth_method=TemporalAuthMethod.MTLS)

        with pytest.raises(ValueError, match="requires tls_secret_name"):
            ExternalTemporalConfig(
                address="temporal.example.com:7233",
                auth_method=TemporalAuthMethod.MTLS,
            )

        config = ExternalTemporalConfig(
            address="temporal.example.com:7233",
            namespace="network-automation",
            auth_method=TemporalAuthMethod.MTLS,
            tls_secret_name="temporal-client-tls",
            tls_server_name="temporal.example.com",
        )

        assert config.tls_server_name == "temporal.example.com"

    @pytest.mark.parametrize("server_name", ['"temporal.example.com"', "temporal\n[other]"])
    def test_external_temporal_rejects_unsafe_tls_server_name(self, server_name: str):
        with pytest.raises(ValueError, match="tls_server_name may contain only"):
            ExternalTemporalConfig(tls_server_name=server_name)

    @pytest.mark.parametrize(
        "namespace", ["network\nautomation", "network\rautomation", "network\x00automation"]
    )
    def test_external_temporal_rejects_unsafe_namespace(self, namespace: str):
        with pytest.raises(ValueError, match="namespace must not contain control characters"):
            ExternalTemporalConfig(namespace=namespace)

    def test_ztp_image_rejects_unsupported_platform(self):
        with pytest.raises(ValueError, match="Unsupported ZTP platform 'sonic'"):
            ZTPOSImage(platform="sonic", version="4.0.0", path="/images/sonic.bin")

    def test_ztp_image_accepts_nv_os_platform(self):
        image = ZTPOSImage(platform="nv-os", version="25.02.2344", path="/images/nv-os.bin")

        assert image.platform == "nv-os"

    def test_yaml_roundtrip(self):
        config = NVConfigManagerInstallConfig(
            cluster=ClusterConfig(
                hostname="test.example.com",
                airgapped=True,
                size=DeploySize.MEDIUM,
                service_account_eks_role="arn:aws:iam::123456789012:role/nv-config-manager-s3",
            ),
            secrets=SecretsConfig(config_manager_service_username="myuser"),
            network_secrets=[
                NetworkSecretEntry(
                    name="BGP Password",
                    description="BGP peering auth",
                    secret_key="bgp_password",
                    required=True,
                ),
            ],
            sites=[SiteConfig(name="dc01")],
            sso=SSOConfig(
                enabled=True,
                provider=SSOProvider.KEYCLOAK,
                issuer_url="https://kc.test/realms/nv-config-manager",
                cli_client_id="nv-config-manager-cli",
            ),
        )

        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            path = Path(f.name)

        try:
            config.to_yaml(path)
            loaded = NVConfigManagerInstallConfig.from_yaml(path)

            assert loaded.cluster.hostname == "test.example.com"
            assert loaded.cluster.airgapped is True
            assert loaded.cluster.size == DeploySize.MEDIUM
            assert (
                loaded.cluster.service_account_eks_role
                == "arn:aws:iam::123456789012:role/nv-config-manager-s3"
            )
            assert loaded.secrets.config_manager_service_username == "myuser"
            assert len(loaded.network_secrets) == 1
            assert loaded.network_secrets[0].secret_key == "bgp_password"
            assert loaded.network_secrets[0].description == "BGP peering auth"
            assert loaded.sites[0].name == "dc01"
            assert loaded.sso.enabled is True
            assert loaded.sso.provider == SSOProvider.KEYCLOAK
            assert loaded.sso.cli_client_id == "nv-config-manager-cli"
        finally:
            path.unlink(missing_ok=True)

    def test_yaml_str(self):
        config = NVConfigManagerInstallConfig(cluster=ClusterConfig(hostname="h.test"))
        text = config.to_yaml_str()
        assert "h.test" in text
        assert "version:" in text

    def test_redis_metrics_enabled_yaml_roundtrip(self, tmp_path: Path):
        path = tmp_path / "config.yaml"
        config = NVConfigManagerInstallConfig(
            infrastructure=InfrastructureConfig(
                monitoring=MonitoringConfig(redis_metrics_enabled=True),
            ),
        )

        config.to_yaml(path)
        loaded = NVConfigManagerInstallConfig.from_yaml(path)

        assert loaded.infrastructure.monitoring.redis_metrics_enabled is True
        assert (
            yaml.safe_load(path.read_text())["infrastructure"]["monitoring"][
                "redis_metrics_enabled"
            ]
            is True
        )

    def test_yaml_prunes_inactive_secret_backend(self):
        config = NVConfigManagerInstallConfig(secrets=SecretsConfig(method=SecretsMethod.ESO))
        config.secrets.k8s.nautobot.values["token"] = "stale-k8s-token"
        config.secrets.vault.auth.token_secret_name = "stale-token-auth-secret"
        config.secrets.vault.paths.slack.path = "stale/slack/path"

        data = yaml.safe_load(config.to_yaml_str())

        assert data["secrets"]["method"] == "eso"
        assert "vault" in data["secrets"]
        assert data["secrets"]["k8s"]["nautobot"]["values"]["token"] == "stale-k8s-token"
        assert (
            NVConfigManagerInstallConfig.model_validate(data).secrets.k8s.nautobot.values["token"]
            == "stale-k8s-token"
        )
        assert "token_secret_name" not in data["secrets"]["vault"]["auth"]
        assert data["secrets"]["vault"]["paths"]["slack"] == {"enabled": False}

        config.secrets.vault.auth.method = VaultAuthMethod.TOKEN
        config.secrets.vault.mount_path = "auth/kubernetes/prod"
        config.secrets.vault.role = "nv-config-manager"

        data = yaml.safe_load(config.to_yaml_str())

        assert "mount_path" not in data["secrets"]["vault"]
        assert "role" not in data["secrets"]["vault"]
        assert data["secrets"]["vault"]["auth"]["token_secret_name"] == "stale-token-auth-secret"

        config.secrets.method = SecretsMethod.KUBERNETES
        config.secrets.vault.server = "https://vault.example.com"
        config.sites = [SiteConfig(name="dc01", vault_path="prod/site/dc01")]
        config.git_tokens = [
            GitTokenEntry(name="prismo", token="tok", vault_path="prod/git/prismo")
        ]

        data = yaml.safe_load(config.to_yaml_str())

        assert data["secrets"]["method"] == "kubernetes"
        assert "k8s" in data["secrets"]
        assert "vault" not in data["secrets"]
        assert data["sites"] == [{"name": "dc01"}]
        assert "vault_path" not in data["git_tokens"][0]

    def test_yaml_prunes_inactive_load_balancer_branches(self):
        lb = LoadBalancerConfig(
            provider=LBProvider.NLB,
            ztp_lb_ip="192.0.2.10",
            ztp_dns_name="ztp.example.com",
            dhcp_lb_ip="192.0.2.11",
            dhcp_dns_name="dhcp.example.com",
            allowed_prefixes=["10.0.0.0/8"],
        )
        lb.nlb_gateway.name = "nv-config-manager-gateway"
        lb.nlb_ztp.name = "nv-config-manager-ztp"
        config = NVConfigManagerInstallConfig(
            infrastructure=InfrastructureConfig(load_balancer=lb),
        )

        data = yaml.safe_load(config.to_yaml_str())
        lb_data = data["infrastructure"]["load_balancer"]

        assert lb_data["provider"] == "nlb"
        assert "nlb_gateway" in lb_data
        assert "nlb_ztp" in lb_data
        assert "ztp_lb_ip" not in lb_data
        assert "allowed_prefixes" not in lb_data

        config.infrastructure.load_balancer.provider = LBProvider.METALLB
        config.infrastructure.load_balancer.ztp_lb_ip = "192.0.2.10"

        data = yaml.safe_load(config.to_yaml_str())
        lb_data = data["infrastructure"]["load_balancer"]

        assert lb_data["provider"] == "metallb"
        assert lb_data["ztp_lb_ip"] == "192.0.2.10"
        assert "nlb_gateway" not in lb_data
        assert "nlb_ztp" not in lb_data

    def test_kgateway_rejects_gateway_nlb_configuration(self):
        lb = LoadBalancerConfig(provider=LBProvider.NLB)
        lb.nlb_gateway.name = "nv-config-manager-gateway"

        with pytest.raises(ValueError, match="Gateway AWS NLB configuration"):
            InfrastructureConfig(gateway=GatewayType.KGATEWAY, load_balancer=lb)

    def test_yaml_prunes_other_disabled_and_alternate_sections(self):
        config = NVConfigManagerInstallConfig(
            sso=SSOConfig(enabled=False, issuer_url="https://issuer.example.com"),
            spiffe=SPIFFEConfig(
                enabled=False,
                provider=SPIFFEProvider.TELEPORT,
                trust_domain="example.com",
            ),
            external_services=ExternalServicesConfig(
                redis=ExternalRedisConfig(enabled=False, host="redis.example.com"),
                postgres=ExternalPostgresConfig(
                    enabled=False,
                    temporal_host="postgres.example.com",
                ),
            ),
            infrastructure=InfrastructureConfig(
                cnpg_s3_backup=CNPGBackupConfig(
                    enabled=False,
                    bucket="old-bucket",
                    endpoint="https://s3.example.com",
                ),
                ztp_storage=ZTPStorageConfig(
                    type=ZTPStorageType.S3,
                    s3_bucket="firmware-images",
                    pvc_name="old-pvc",
                    os_images=[ZTPOSImage(path="/tmp/image.bin")],
                ),
            ),
            images=ImagesConfig(
                source=ImageSource.LOCAL,
                registry="old.example.com/nv-config-manager",
                tag="old",
                pull_secret=ImagePullSecret(password="secret"),
                overrides={"nvConfigManager": ImageOverride(tag="old")},
            ),
        )

        data = yaml.safe_load(config.to_yaml_str())

        assert data["sso"] == {"enabled": False}
        assert data["spiffe"] == {"enabled": False}
        assert data["external_services"]["redis"] == {"enabled": False}
        assert data["external_services"]["postgres"] == {"enabled": False}
        assert data["infrastructure"]["cnpg_s3_backup"] == {"enabled": False}
        assert data["infrastructure"]["ztp_storage"] == {
            "type": "s3",
            "s3_bucket": "firmware-images",
        }
        assert data["images"] == {"source": "local"}

    def test_network_secrets_defaults(self):
        config = NVConfigManagerInstallConfig()
        assert config.network_secrets == []

    def test_network_secrets_list(self):
        config = NVConfigManagerInstallConfig(
            network_secrets=[
                NetworkSecretEntry(name="BGP Password", secret_key="bgp_password"),
                NetworkSecretEntry(name="Custom", secret_key="my_custom_key"),
            ]
        )
        assert len(config.network_secrets) == 2
        assert config.network_secrets[0].secret_key == "bgp_password"
        assert config.network_secrets[1].name == "Custom"

    def test_all_services_toggle(self):
        config = NVConfigManagerInstallConfig()
        config.services.render = False
        config.services.ztp = False
        assert config.services.render is False
        assert config.services.nautobot is True

    def test_custom_jobs_require_local_nautobot(self):
        with pytest.raises(ValueError, match="Custom jobs.*require a local Nautobot"):
            NVConfigManagerInstallConfig(
                services=ServicesConfig(
                    nautobot=False, external_nautobot_url="https://nb.example.com"
                ),
                content=ContentConfig(jobs=[{"path": "jobs/my_job"}]),
            )

    def test_post_deploy_jobs_require_local_nautobot(self):
        with pytest.raises(ValueError, match="post-deploy jobs require a local Nautobot"):
            NVConfigManagerInstallConfig(
                services=ServicesConfig(
                    nautobot=False, external_nautobot_url="https://nb.example.com"
                ),
                content=ContentConfig(run_after_deploy=[{"job": "jobs.bootstrap.SiteBootstrap"}]),
            )

    def test_external_nautobot_valid_without_jobs(self):
        config = NVConfigManagerInstallConfig(
            services=ServicesConfig(nautobot=False, external_nautobot_url="https://nb.example.com"),
            content=ContentConfig(jobs=[]),
        )
        assert config.services.nautobot is False
        assert config.services.external_nautobot_url == "https://nb.example.com"

    def test_external_nautobot_requires_server(self):
        with pytest.raises(ValueError, match="dcim.server or services.external_nautobot_url"):
            NVConfigManagerInstallConfig(services=ServicesConfig(nautobot=False))

        config = NVConfigManagerInstallConfig(
            dcim=DCIMConfig(server="https://nb.example.com"),
            services=ServicesConfig(nautobot=False),
        )

        assert config.dcim.server == "https://nb.example.com"

    def test_external_dcim_requires_disabled_nautobot_and_server(self):
        with pytest.raises(ValueError, match="services.nautobot=false"):
            NVConfigManagerInstallConfig(
                dcim=DCIMConfig(provider="synthetic", server="https://dcim")
            )

        with pytest.raises(ValueError, match="dcim.server is required"):
            NVConfigManagerInstallConfig(
                dcim=DCIMConfig(provider="synthetic"),
                services=ServicesConfig(nautobot=False),
            )

        config = NVConfigManagerInstallConfig(
            dcim=DCIMConfig(provider="synthetic", server="https://synthetic.example"),
            services=ServicesConfig(nautobot=False),
        )

        assert config.dcim.provider == "synthetic"

    def test_external_dcim_eso_requires_provider_secret_path(self):
        with pytest.raises(ValueError, match="paths.dcim.enabled"):
            NVConfigManagerInstallConfig(
                dcim=DCIMConfig(provider="synthetic", server="https://synthetic.example"),
                services=ServicesConfig(nautobot=False),
                secrets=SecretsConfig(method=SecretsMethod.ESO),
            )


class TestImagesConfig:
    def test_defaults(self):
        config = NVConfigManagerInstallConfig()
        img = config.images
        assert img.source == ImageSource.REGISTRY
        assert img.registry == "nvcr.io/nvidian/cfa"
        assert img.tag == ""
        assert img.pull_policy == "IfNotPresent"
        assert img.pull_secret.name == "regcred-nvcr"
        assert img.pull_secret.server == "nvcr.io"
        assert img.pull_secret.username == "$oauthtoken"
        assert img.pull_secret.password == ""
        assert img.kind_preload_images == []
        assert img.overrides == {}

    def test_local_source(self):
        config = NVConfigManagerInstallConfig(images=ImagesConfig(source=ImageSource.LOCAL))
        assert config.images.source == ImageSource.LOCAL

    def test_custom_registry(self):
        config = NVConfigManagerInstallConfig(
            images=ImagesConfig(
                registry="registry.corp.com/nv-config-manager",
                tag="v2.1.0",
                pull_secret=ImagePullSecret(
                    name="registry-creds",
                    server="registry.corp.com",
                    username="robot$ci",
                    password="hunter2",
                ),
            )
        )
        img = config.images
        assert img.registry == "registry.corp.com/nv-config-manager"
        assert img.tag == "v2.1.0"
        assert img.pull_secret.name == "registry-creds"
        assert img.pull_secret.server == "registry.corp.com"

    def test_per_image_overrides(self):
        config = NVConfigManagerInstallConfig(
            images=ImagesConfig(
                overrides={
                    "nautobot": ImageOverride(repository="my-reg/my-nautobot", tag="custom"),
                    "nvConfigManager": ImageOverride(tag="dev-branch"),
                }
            )
        )
        assert config.images.overrides["nautobot"].repository == "my-reg/my-nautobot"
        assert config.images.overrides["nautobot"].tag == "custom"
        assert config.images.overrides["nvConfigManager"].tag == "dev-branch"
        assert config.images.overrides["nvConfigManager"].repository == ""

    def test_temporal_bootstrap_image_override_is_rejected(self):
        with pytest.raises(ValueError, match="temporalBootstrap is not supported"):
            ImagesConfig(
                overrides={
                    "temporalBootstrap": ImageOverride(repository="registry.example/bootstrap")
                }
            )

    def test_roundtrip_with_overrides(self):
        config = NVConfigManagerInstallConfig(
            cluster=ClusterConfig(hostname="test.com"),
            images=ImagesConfig(
                source=ImageSource.REGISTRY,
                registry="registry.io/team",
                tag="v1.0",
                pull_secret=ImagePullSecret(name="my-creds", password="secret"),
                overrides={"nvConfigManager": ImageOverride(tag="special")},
            ),
        )
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            path = Path(f.name)
        try:
            config.to_yaml(path)
            loaded = NVConfigManagerInstallConfig.from_yaml(path)
            assert loaded.images.source == ImageSource.REGISTRY
            assert loaded.images.registry == "registry.io/team"
            assert loaded.images.tag == "v1.0"
            assert loaded.images.pull_secret.name == "my-creds"
            assert loaded.images.pull_secret.password == "secret"
            assert loaded.images.overrides["nvConfigManager"].tag == "special"
        finally:
            path.unlink(missing_ok=True)

    def test_kind_preload_images_roundtrip_for_local_source(self):
        config = NVConfigManagerInstallConfig(
            images=ImagesConfig(
                source=ImageSource.LOCAL,
                registry="old.example.com/nv-config-manager",
                tag="old",
                kind_preload_images=["docker.io/library/redis:7-alpine"],
                pull_secret=ImagePullSecret(password="secret"),
                overrides={"nvConfigManager": ImageOverride(tag="old")},
            )
        )

        data = yaml.safe_load(config.to_yaml_str())
        assert data["images"] == {
            "source": "local",
            "kind_preload_images": ["docker.io/library/redis:7-alpine"],
        }

        loaded = NVConfigManagerInstallConfig.model_validate(data)
        assert loaded.images.kind_preload_images == ["docker.io/library/redis:7-alpine"]

    def test_empty_password_optional(self):
        config = NVConfigManagerInstallConfig(
            images=ImagesConfig(
                pull_secret=ImagePullSecret(password=""),
            )
        )
        assert config.images.pull_secret.password == ""


class TestGitTokenConfig:
    def test_defaults(self):
        config = NVConfigManagerInstallConfig()
        assert config.git_tokens == []

    def test_basic_git_token(self):
        config = NVConfigManagerInstallConfig(
            git_tokens=[GitTokenEntry(name="prismo", token="ghp_abc123")]
        )
        assert len(config.git_tokens) == 1
        assert config.git_tokens[0].name == "prismo"
        assert config.git_tokens[0].token == "ghp_abc123"
        assert config.git_tokens[0].username == ""
        assert config.git_tokens[0].vault_path == ""

    def test_git_token_with_username(self):
        config = NVConfigManagerInstallConfig(
            git_tokens=[GitTokenEntry(name="prismo", token="tok", username="bot-user")]
        )
        assert config.git_tokens[0].username == "bot-user"

    def test_git_token_yaml_roundtrip(self, tmp_path):
        path = tmp_path / "config.yaml"
        config = NVConfigManagerInstallConfig(
            secrets=SecretsConfig(method=SecretsMethod.ESO),
            git_tokens=[
                GitTokenEntry(
                    name="prismo",
                    token="tok123",
                    username="bot",
                    vault_path="nv-config-manager/git",
                ),
                GitTokenEntry(name="gitlab", token="glpat-xyz"),
            ],
        )
        config.to_yaml(path)
        loaded = NVConfigManagerInstallConfig.from_yaml(path)
        assert len(loaded.git_tokens) == 2
        assert loaded.git_tokens[0].name == "prismo"
        assert loaded.git_tokens[0].vault_path == "nv-config-manager/git"
        assert loaded.git_tokens[1].name == "gitlab"

    def test_ztp_storage_defaults(self):
        config = NVConfigManagerInstallConfig()
        zs = config.infrastructure.ztp_storage
        assert zs.type == ZTPStorageType.FILE
        assert zs.pvc_name == "ztp-os-images"
        assert zs.pvc_size == "10Gi"
        assert zs.storage_class == ""
        assert zs.s3_bucket == ""
        assert zs.s3_endpoint == ""
        assert zs.s3_region == ""
        assert zs.s3_ceph.enabled is False
        assert zs.os_images == []

    def test_jobs_config_defaults(self):
        config = NVConfigManagerInstallConfig()
        jc = config.content.jobs_config
        assert jc.storage_class == ""
        assert jc.access_mode == "ReadWriteOnce"
        assert jc.node_selector == {}

    def test_ztp_storage_file_config(self):
        config = NVConfigManagerInstallConfig(
            infrastructure=InfrastructureConfig(
                ztp_storage=ZTPStorageConfig(
                    type=ZTPStorageType.FILE,
                    pvc_name="my-images",
                    pvc_size="50Gi",
                    storage_class="fast-ssd",
                    os_images=[
                        ZTPOSImage(
                            platform="cumulus-linux", version="5.14.0", path="/images/fw1.bin"
                        ),
                        ZTPOSImage(platform="mlnx-os", version="3.10.4000", path="/images/fw2.bin"),
                    ],
                )
            )
        )
        zs = config.infrastructure.ztp_storage
        assert zs.type == ZTPStorageType.FILE
        assert zs.pvc_name == "my-images"
        assert zs.pvc_size == "50Gi"
        assert zs.storage_class == "fast-ssd"
        assert len(zs.os_images) == 2
        assert zs.os_images[0].platform == "cumulus-linux"
        assert zs.os_images[0].version == "5.14.0"
        assert zs.os_images[1].platform == "mlnx-os"

    def test_ztp_storage_yaml_roundtrip(self, tmp_path):
        path = tmp_path / "config.yaml"
        config = NVConfigManagerInstallConfig(
            infrastructure=InfrastructureConfig(
                ztp_storage=ZTPStorageConfig(
                    type=ZTPStorageType.FILE,
                    pvc_name="my-pvc",
                    pvc_size="20Gi",
                    os_images=[
                        ZTPOSImage(
                            platform="cumulus-linux", version="5.9.0", path="/path/to/image.bin"
                        )
                    ],
                )
            )
        )
        config.to_yaml(path)
        loaded = NVConfigManagerInstallConfig.from_yaml(path)
        zs = loaded.infrastructure.ztp_storage
        assert zs.type == ZTPStorageType.FILE
        assert zs.pvc_name == "my-pvc"
        assert zs.pvc_size == "20Gi"
        assert len(zs.os_images) == 1
        assert zs.os_images[0].platform == "cumulus-linux"
        assert zs.os_images[0].version == "5.9.0"
        assert zs.os_images[0].path == "/path/to/image.bin"

    def test_ztp_s3_storage_yaml_roundtrip(self, tmp_path):
        path = tmp_path / "config.yaml"
        config = NVConfigManagerInstallConfig(
            infrastructure=InfrastructureConfig(
                ztp_storage=ZTPStorageConfig(
                    type=ZTPStorageType.S3,
                    s3_bucket="firmware-images",
                    s3_endpoint="https://minio.example",
                    s3_region="us-west-2",
                )
            )
        )
        config.to_yaml(path)
        loaded = NVConfigManagerInstallConfig.from_yaml(path)
        zs = loaded.infrastructure.ztp_storage
        assert zs.type == ZTPStorageType.S3
        assert zs.s3_bucket == "firmware-images"
        assert zs.s3_endpoint == "https://minio.example"
        assert zs.s3_region == "us-west-2"

    def test_ztp_ceph_storage_yaml_keeps_bucket_and_prunes_endpoint(self, tmp_path):
        path = tmp_path / "config.yaml"
        config = NVConfigManagerInstallConfig(
            infrastructure=InfrastructureConfig(
                ztp_storage=ZTPStorageConfig(
                    type=ZTPStorageType.S3,
                    s3_bucket="firmware-images",
                    s3_endpoint="https://ignored.example",
                    s3_ceph=ZTPS3CephConfig(
                        enabled=True,
                        object_bucket_claim=ZTPS3CephObjectBucketClaimConfig(
                            storage_class_name="ceph-object-store"
                        ),
                    ),
                )
            )
        )
        config.to_yaml(path)
        data = yaml.safe_load(path.read_text())
        ztp_storage = data["infrastructure"]["ztp_storage"]
        assert ztp_storage["type"] == "s3"
        assert ztp_storage["s3_bucket"] == "firmware-images"
        assert "s3_endpoint" not in ztp_storage
        assert ztp_storage["s3_ceph"]["enabled"] is True
        assert (
            ztp_storage["s3_ceph"]["object_bucket_claim"]["storage_class_name"]
            == "ceph-object-store"
        )

    def test_redfish_defaults(self):
        config = NVConfigManagerInstallConfig()
        assert config.redfish.enabled is False
        assert config.redfish.vendors == {}

    def test_redfish_with_vendors(self):
        config = NVConfigManagerInstallConfig(
            redfish=RedfishConfig(
                enabled=True,
                vendors={
                    "lenovo": RedfishVendorCreds(
                        default_user="admin", default_password="pw1", config_manager_password="pw2"
                    ),
                    "bluefield": RedfishVendorCreds(),
                },
            )
        )
        assert config.redfish.enabled is True
        assert len(config.redfish.vendors) == 2
        assert config.redfish.vendors["lenovo"].default_user == "admin"
        assert config.redfish.vendors["bluefield"].default_user == ""

    def test_redfish_yaml_roundtrip(self, tmp_path):
        path = tmp_path / "config.yaml"
        config = NVConfigManagerInstallConfig(
            redfish=RedfishConfig(
                enabled=True,
                vendors={
                    "lenovo": RedfishVendorCreds(
                        default_user="admin",
                        default_password="secret",
                        config_manager_password="kpw",
                    ),
                },
            )
        )
        config.to_yaml(path)
        loaded = NVConfigManagerInstallConfig.from_yaml(path)
        assert loaded.redfish.enabled is True
        assert loaded.redfish.vendors["lenovo"].default_user == "admin"
        assert loaded.redfish.vendors["lenovo"].config_manager_password == "kpw"
