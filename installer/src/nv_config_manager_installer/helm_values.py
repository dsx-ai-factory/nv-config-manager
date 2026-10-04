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
"""Helm values-generated.yaml builder.

Generates Helm values from installer configuration, consuming
the Pydantic config model and generated secrets to produce the Helm values file.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from nv_config_manager_installer.accounts import build_eso_config_secrets
from nv_config_manager_installer.schema import (
    BUILT_IN_NAUTOBOT_PROVIDER,
    NV_CONFIG_MANAGER_IMAGE_KEYS,
    ExternalPostgresConfig,
    ImageSource,
    LBProvider,
    NLBServiceConfig,
    NVConfigManagerInstallConfig,
    SecretsMethod,
    SPIFFEProvider,
    SSOProvider,
    ZTPStorageType,
    get_known_workflows,
)
from nv_config_manager_installer.secrets import build_eso_vault_config

_ZTP_S3_CREDENTIALS_SECRET_NAME = "ztp-s3-credentials"


def _has_ztp_s3_k8s_credentials(config: NVConfigManagerInstallConfig) -> bool:
    """Return true when installer-managed Kubernetes S3 credentials are configured."""
    if config.secrets.method != SecretsMethod.KUBERNETES:
        return False
    ztp_s3 = config.secrets.k8s.ztp_s3
    if not ztp_s3.enabled:
        return False
    return bool(ztp_s3.values.get("accessKeyId") and ztp_s3.values.get("secretAccessKey"))


def _provider_defaults(provider: SSOProvider, client_id: str) -> dict[str, list[str]]:
    """Return provider-specific default scopes and audiences.

    Azure AD requires ``api://{client_id}/access`` scope to issue v2 access
    tokens whose ``iss`` matches the v2 issuer.  Without it, Azure issues v1
    tokens (``iss: https://sts.windows.net/{tenant}/``) which fail JWT
    validation against the v2 issuer URL.
    """
    if provider == SSOProvider.AZURE:
        return {
            "scopes": ["openid", "email", "profile", f"api://{client_id}/access"],
            "audiences": [f"api://{client_id}", client_id],
        }
    if provider == SSOProvider.KEYCLOAK:
        return {
            "scopes": ["openid", "email", "profile"],
            "audiences": ["account", client_id],
        }
    return {
        "scopes": ["openid", "email", "profile"],
        "audiences": [client_id],
    }


def _derive_oidc_endpoints(provider: SSOProvider, issuer_url: str) -> dict[str, str]:
    """Compute provider-specific OIDC endpoints from the issuer URL.

    The Helm chart's SecurityPolicy defaults to Keycloak-style paths for the
    authorization/token endpoints, but the nv-config-manager-auth sidecar INI config does
    **not** auto-derive ``jwks_uri`` — it must always be set explicitly.

    Azure AD issuer is typically
    ``https://login.microsoftonline.com/{tenant}/v2.0`` and the endpoints live
    at ``https://login.microsoftonline.com/{tenant}/oauth2/v2.0/*``.
    """
    issuer = issuer_url.rstrip("/")
    if not issuer:
        return {}

    if provider == SSOProvider.AZURE:
        base = issuer.removesuffix("/v2.0")
        return {
            "authorizationEndpoint": f"{base}/oauth2/v2.0/authorize",
            "tokenEndpoint": f"{base}/oauth2/v2.0/token",
            "endSessionEndpoint": f"{base}/oauth2/v2.0/logout",
            "jwksUri": f"{base}/discovery/v2.0/keys",
        }

    if provider == SSOProvider.KEYCLOAK:
        return {
            "authorizationEndpoint": f"{issuer}/protocol/openid-connect/auth",
            "tokenEndpoint": f"{issuer}/protocol/openid-connect/token",
            "endSessionEndpoint": f"{issuer}/protocol/openid-connect/logout",
            "jwksUri": f"{issuer}/protocol/openid-connect/certs",
        }

    # Generic: standard OIDC discovery path
    return {
        "jwksUri": f"{issuer}/.well-known/jwks.json",
    }


def _build_git_tokens(config: NVConfigManagerInstallConfig) -> list[dict[str, Any]]:
    """Build the ``secrets.vault.paths.gitTokens`` array for Helm values."""
    result: list[dict[str, Any]] = []
    for gt in config.git_tokens:
        if not gt.name:
            continue
        entry: dict[str, Any] = {
            "name": gt.name,
            "secretName": f"git-token-{gt.name.lower()}",
            "hasUsername": bool(gt.username),
        }
        if gt.vault_path:
            entry["path"] = gt.vault_path
        result.append(entry)
    return result


def _build_nlb_service_values(nlb: NLBServiceConfig) -> dict[str, Any]:
    """Build the NLB values dict for a single service (ZTP or DHCP).

    Returns an empty dict if no meaningful NLB fields are set.
    """
    if not nlb.name and not nlb.sg and not nlb.subnets:
        return {}
    section: dict[str, Any] = {
        "type": nlb.type or "external",
        "target_type": nlb.target_type or "ip",
    }
    if nlb.name:
        section["name"] = nlb.name
    if nlb.sg:
        section["sg"] = nlb.sg
    if nlb.subnets:
        section["subnets"] = nlb.subnets
    if nlb.ips:
        section["ips"] = nlb.ips
    if nlb.dns_name:
        section["dns_name"] = nlb.dns_name
    return section


_DEFAULT_IMAGE_REGISTRY = "nvcr.io/nvidian/cfa"

_GLOBAL_IMAGE_DEFAULTS: dict[str, tuple[str, str]] = {
    "httpEcho": ("docker.io/hashicorp/http-echo", "1.0"),
    "kubectl": ("docker.io/alpine/kubectl", "1.35.4"),
    "busybox": ("docker.io/library/busybox", "1.36"),
    "redis": ("docker.io/library/redis", "7-alpine"),
    "redisExporter": ("docker.io/oliver006/redis_exporter", "v1.90.0"),
    "nats": ("docker.io/library/nats", "2.14-alpine"),
    "natsBox": ("docker.io/natsio/nats-box", "0.14.3"),
    "natsExporter": ("docker.io/natsio/prometheus-nats-exporter", "0.20.1"),
}

_NESTED_IMAGE_DEFAULTS: dict[str, tuple[tuple[str, ...], str, str]] = {
    "spiffeHelper": (("spiffe", "helper", "image"), "ghcr.io/spiffe/spiffe-helper", "0.8.0"),
    "oidcProxy": (
        ("oidc", "proxy", "image"),
        "quay.io/oauth2-proxy/oauth2-proxy",
        "v7.15.4",
    ),
    "prometheusServer": (
        ("prometheus", "server", "image"),
        "quay.io/prometheus/prometheus",
        "v3.11.3",
    ),
    "prometheusConfigReloader": (
        ("prometheus", "configmapReload", "prometheus", "image"),
        "quay.io/prometheus-operator/prometheus-config-reloader",
        "v0.90.1",
    ),
}

_SPLIT_IMAGE_DEFAULTS: dict[str, tuple[tuple[str, ...], str, str]] = {
    "nautobotNginx": (
        ("nautobot", "nginx", "image"),
        "docker.io/nginxinc/nginx-unprivileged",
        "1.27",
    ),
    "alloy": (
        ("alloy", "image"),
        "docker.io/grafana/alloy",
        "v1.16.0",
    ),
    "alloyConfigReloader": (
        ("alloy", "configReloader", "image"),
        "quay.io/prometheus-operator/prometheus-config-reloader",
        "v0.90.1",
    ),
}

_STRING_IMAGE_DEFAULTS: dict[str, tuple[tuple[str, ...], str, str]] = {
    "templatePluginInstaller": (
        ("renderService", "templatePlugins", "installerImage"),
        "docker.io/library/python",
        "3.13-alpine",
    ),
    "dcimProviderInstaller": (
        ("dcim", "providerPackages", "installerImage"),
        "docker.io/library/python",
        "3.13-bookworm",
    ),
    "envoyProxy": (
        ("gateway", "envoyProxy", "image"),
        "docker.io/envoyproxy/envoy",
        "distroless-v1.36.5",
    ),
    "postgresql": (
        ("cnpg", "imageName"),
        "ghcr.io/cloudnative-pg/postgresql",
        "18.0-system-trixie",
    ),
    "pgbouncer": (
        ("cnpg", "poolerImageName"),
        "ghcr.io/cloudnative-pg/pgbouncer",
        "1.22.1",
    ),
}


def _strip_image_registry(repository: str) -> str:
    """Return repository path with any registry host removed."""
    first, sep, rest = repository.partition("/")
    if sep and ("." in first or ":" in first or first == "localhost"):
        return rest
    return repository


def _registry_repository(registry: str, source_repository: str) -> str:
    """Map a source image repository the same way upload-to-registry.sh does."""
    return f"{registry.rstrip('/')}/{_strip_image_registry(source_repository)}"


def _use_airgap_registry_defaults(config: NVConfigManagerInstallConfig) -> bool:
    """Return true when airgap mode should map bundled source paths automatically."""
    return bool(config.cluster.airgapped and config.images.registry != _DEFAULT_IMAGE_REGISTRY)


def _build_image_entry(
    img: Any,
    key: str,
    source_repository: str,
    default_tag: str,
    *,
    include_default: bool,
    legacy_short_name: str = "",
    use_global_tag: bool = False,
    airgapped: bool = False,
) -> dict[str, str] | None:
    """Build a Helm image entry from global settings and optional per-key override."""
    ovr = img.overrides.get(key)
    if not ovr and not include_default:
        return None

    if ovr and ovr.repository:
        repository = ovr.repository
    elif img.registry:
        if legacy_short_name and not airgapped:
            repository = f"{img.registry.rstrip('/')}/{legacy_short_name}"
        else:
            repository = _registry_repository(img.registry, source_repository)
    elif legacy_short_name:
        repository = legacy_short_name
    else:
        repository = source_repository

    tag = ovr.tag if ovr and ovr.tag else (img.tag if use_global_tag and img.tag else default_tag)
    entry = {"repository": repository, "pullPolicy": img.pull_policy}
    if tag:
        entry["tag"] = tag
    return entry


def _image_ref(entry: dict[str, str]) -> str:
    tag = entry.get("tag")
    return f"{entry['repository']}:{tag}" if tag else entry["repository"]


def _split_registry_repository(repository: str) -> tuple[str, str]:
    first, sep, rest = repository.partition("/")
    if sep and ("." in first or ":" in first or first == "localhost"):
        return first, rest
    return "", repository


def _set_nested(values: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    cur: dict[str, Any] = values
    for part in path[:-1]:
        next_value = cur.setdefault(part, {})
        if not isinstance(next_value, dict):
            next_value = {}
            cur[part] = next_value
        cur = next_value
    cur[path[-1]] = value


def _merge_nested(values: dict[str, Any], path: tuple[str, ...], update: dict[str, Any]) -> None:
    cur: dict[str, Any] = values
    for part in path:
        next_value = cur.setdefault(part, {})
        if not isinstance(next_value, dict):
            next_value = {}
            cur[part] = next_value
        cur = next_value
    cur.update(update)


def _apply_image_overrides(config: NVConfigManagerInstallConfig, values: dict[str, Any]) -> None:
    """Apply non-global image overrides that live outside global.images."""
    img = config.images
    include_defaults = _use_airgap_registry_defaults(config)

    for key, (path, source_repository, default_tag) in _NESTED_IMAGE_DEFAULTS.items():
        entry = _build_image_entry(
            img,
            key,
            source_repository,
            default_tag,
            include_default=include_defaults,
            airgapped=include_defaults,
        )
        if entry:
            _merge_nested(values, path, entry)

    for key, (path, source_repository, default_tag) in _SPLIT_IMAGE_DEFAULTS.items():
        entry = _build_image_entry(
            img,
            key,
            source_repository,
            default_tag,
            include_default=include_defaults,
            airgapped=include_defaults,
        )
        if entry:
            registry, repository = _split_registry_repository(entry["repository"])
            update = {"repository": repository, "tag": entry.get("tag", default_tag)}
            if registry:
                update["registry"] = registry
            update["pullPolicy"] = entry["pullPolicy"]
            _merge_nested(values, path, update)

    for key, (path, source_repository, default_tag) in _STRING_IMAGE_DEFAULTS.items():
        entry = _build_image_entry(
            img,
            key,
            source_repository,
            default_tag,
            include_default=include_defaults,
            airgapped=include_defaults,
        )
        if entry:
            _set_nested(values, path, _image_ref(entry))


def _build_global(
    config: NVConfigManagerInstallConfig,
    *,
    is_local: bool,
    local_tags: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build the ``global`` section of Helm values."""
    c = config.cluster
    img = config.images
    section: dict[str, Any] = {
        "environment": c.environment,
        "baseDomain": c.hostname,
        "namespace": c.namespace,
        "aggregate": False,
        "createNamespace": False,
        "createServiceAccount": True,
        "serviceAccountName": "vault-access-sa",
    }
    if c.service_account_eks_role:
        section["serviceAccountEksRole"] = c.service_account_eks_role

    if c.environment == "local":
        section["deploymentStrategy"] = {"type": "Recreate"}

    if is_local:
        section["imagePullSecrets"] = []
        section["imagePullPolicy"] = "IfNotPresent"
    else:
        section["imagePullSecrets"] = [img.pull_secret.name]
        section["imagePullPolicy"] = img.pull_policy

    if is_local:
        section["images"] = {
            key: {
                "repository": short_name,
                "tag": (local_tags or {}).get(short_name, "local"),
                "pullPolicy": "IfNotPresent",
            }
            for key, short_name in NV_CONFIG_MANAGER_IMAGE_KEYS
        }
    elif img.registry or img.tag or img.overrides:
        section["images"] = _build_registry_images(
            img, airgapped=_use_airgap_registry_defaults(config)
        )
    return section


def _build_registry_images(img: Any, *, airgapped: bool = False) -> dict[str, Any]:
    """Build the per-image entries for ``global.images``."""
    images: dict[str, Any] = {}
    for key, short_name in NV_CONFIG_MANAGER_IMAGE_KEYS:
        source_repository = f"nvcr.io/nvidian/cfa/{short_name}"
        entry = _build_image_entry(
            img,
            key,
            source_repository,
            "",
            include_default=True,
            legacy_short_name=short_name,
            use_global_tag=True,
            airgapped=airgapped,
        )
        if entry:
            images[key] = entry

    for key, (source_repository, default_tag) in _GLOBAL_IMAGE_DEFAULTS.items():
        entry = _build_image_entry(
            img,
            key,
            source_repository,
            default_tag,
            include_default=airgapped,
            airgapped=airgapped,
        )
        if entry:
            images[key] = entry
    return images


def _build_secrets(config: NVConfigManagerInstallConfig) -> dict[str, Any]:
    """Build the ``secrets`` top-level section."""
    if config.secrets.method == SecretsMethod.ESO:
        return build_eso_vault_config(config)
    return {"secrets": {"method": "kubernetes"}}


def _build_gateway(config: NVConfigManagerInstallConfig) -> dict[str, Any]:
    """Build the Gateway API controller and ``gateway`` Helm values sections."""
    lb = config.infrastructure.load_balancer
    gateway_type = config.infrastructure.gateway.value
    default_class_name = "kgateway" if gateway_type == "kgateway" else "envoy-gateway"
    gateway: dict[str, Any] = {
        "enabled": True,
        "create": config.infrastructure.create_gateway,
        "name": config.infrastructure.gateway_name,
        "namespace": config.infrastructure.gateway_namespace,
        "sectionName": config.infrastructure.gateway_listener,
        "className": config.infrastructure.gateway_class_name or default_class_name,
        "baseHostname": config.cluster.hostname,
        "createGatewayClass": (
            config.infrastructure.create_gateway_class and gateway_type == "envoyGateway"
        ),
        "certificates": {"enabled": config.infrastructure.tls},
    }
    if config.infrastructure.tls:
        gateway["certificates"]["selfSigned"] = True
        # The private CA chain is only for the installer-managed local profiles.
        # Preserve the legacy self-signed issuer for every other environment.
        if config.cluster.environment.startswith("local"):
            gateway["certificates"]["localCA"] = True
    if lb.provider == LBProvider.NONE:
        gateway["nodePort"] = {"enabled": True, "http": 30080, "https": 30443}
    if lb.provider == LBProvider.NLB and gateway_type == "envoyGateway":
        gateway["nlb"] = _build_nlb_service_values(lb.nlb_gateway)

    gateway["auth"] = {"jwt": _build_jwt_section(config)}
    gateway["rateLimit"] = {"enabled": False}
    return {"ingress": {"type": gateway_type}, "gateway": gateway}


def _build_jwt_section(config: NVConfigManagerInstallConfig) -> dict[str, Any]:
    """Build the ``gateway.auth.jwt`` section using the providers[] array format."""
    jwt: dict[str, Any] = {"enabled": config.sso.enabled}
    if not config.sso.enabled:
        return jwt

    providers: list[dict[str, Any]] = []

    # Primary provider synthesized from the main SSO/OIDC config
    sso_defaults = _provider_defaults(config.sso.provider, config.sso.client_id)
    endpoints = _derive_oidc_endpoints(config.sso.provider, config.sso.issuer_url)
    primary: dict[str, Any] = {
        "name": config.sso.provider.value,
        "issuer": config.sso.issuer_url,
        "audiences": (
            config.sso.audiences.split(",") if config.sso.audiences else sso_defaults["audiences"]
        ),
    }
    jwks_uri = config.sso.jwks_uri or endpoints.get("jwksUri", "")
    if jwks_uri:
        primary["jwksUri"] = jwks_uri
    providers.append(primary)

    # Additional JWT providers (e.g. SPIRE, Starfleet)
    for p in config.sso.jwt_providers:
        if not p.name or not p.issuer:
            continue
        entry: dict[str, Any] = {"name": p.name, "issuer": p.issuer}
        if p.audiences:
            entry["audiences"] = [a.strip() for a in p.audiences.split(",")]
        if p.jwks_uri:
            entry["jwksUri"] = p.jwks_uri
        providers.append(entry)

    jwt["providers"] = providers
    return jwt


def _build_spiffe(config: NVConfigManagerInstallConfig) -> dict[str, Any]:
    """Build the ``spiffe`` section of Helm values."""
    section: dict[str, Any] = {"enabled": config.spiffe.enabled}
    if not config.spiffe.enabled:
        return section
    sp = config.spiffe
    section.update(
        {
            "provider": sp.provider.value,
            "authMode": sp.auth_mode.value,
            "trustDomain": sp.trust_domain,
            "socket": {"mountPath": sp.socket_mount_path, "socketFile": sp.socket_file},
        }
    )
    if sp.provider == SPIFFEProvider.TELEPORT:
        section["socket"]["hostPath"] = sp.socket_host_path
    else:
        section["spire"] = {"csiDriver": "csi.spiffe.io"}

    group_map: dict[str, str] = {}
    for entry in sp.group_prefixes:
        if "=" in entry:
            prefix, group = entry.split("=", 1)
            group_map[prefix] = group
    section["rbac"] = {"groupPrefixes": group_map}
    return section


def _build_oidc(config: NVConfigManagerInstallConfig, values: dict[str, Any]) -> None:
    """Populate ``oidc`` and ``localDev`` in *values*."""
    if not config.sso.enabled:
        values["oidc"] = {"enabled": False}
        local_dev: dict[str, Any] = {
            "mockAuth": {
                "enabled": True,
                "email": "dev@localhost",
                "user": "dev@localhost",
                "name": "Local Developer",
                "groups": "nv-config-manager,nv-config-manager-admin",
                "preferredUsername": "dev",
            },
            "mockDevices": config.cluster.mock_devices,
        }
        values["localDev"] = local_dev
        return

    sso_defaults = _provider_defaults(config.sso.provider, config.sso.client_id)
    oidc: dict[str, Any] = {
        "enabled": True,
        "issuerUrl": config.sso.issuer_url,
        "clientId": config.sso.client_id,
        "cliClientId": config.sso.cli_client_id or config.sso.client_id,
        "authUtility": {"enabled": True},
        "audiences": (
            config.sso.audiences.split(",") if config.sso.audiences else sso_defaults["audiences"]
        ),
        "scopes": (config.sso.scopes.split(",") if config.sso.scopes else sso_defaults["scopes"]),
    }
    if config.sso.internal_issuer:
        oidc["internalIssuerUrl"] = config.sso.internal_issuer

    endpoints = _derive_oidc_endpoints(config.sso.provider, config.sso.issuer_url)
    if config.sso.jwks_uri:
        oidc["jwksUri"] = config.sso.jwks_uri
    elif endpoints.get("jwksUri"):
        oidc["jwksUri"] = endpoints["jwksUri"]
    if endpoints.get("authorizationEndpoint"):
        oidc["authorizationEndpoint"] = endpoints["authorizationEndpoint"]
    if endpoints.get("tokenEndpoint"):
        oidc["tokenEndpoint"] = endpoints["tokenEndpoint"]
    if config.sso.end_session_endpoint:
        oidc["endSessionEndpoint"] = config.sso.end_session_endpoint
    elif endpoints.get("endSessionEndpoint"):
        oidc["endSessionEndpoint"] = endpoints["endSessionEndpoint"]
    values["oidc"] = oidc

    if config.cluster.mock_devices:
        local_dev = values.setdefault("localDev", {})
        local_dev["mockDevices"] = True
        local_dev["enableLocalTestWorkflows"] = True


def _build_postgres_section(pg: ExternalPostgresConfig) -> dict[str, Any]:
    """Build the ``externalServices.postgres`` section."""
    temporal_host = pg.temporal_host if (pg.enabled and pg.temporal_host) else "cluster-temporal-rw"

    if pg.enabled and pg.temporal_visibility_host:
        vis_host = pg.temporal_visibility_host
    elif pg.enabled and pg.temporal_host:
        vis_host = pg.temporal_host
    else:
        vis_host = "cluster-temporal-visibility-rw"

    postgres: dict[str, Any] = {"port": pg.port if pg.enabled else 5432}
    postgres["temporal"] = {
        "host": temporal_host,
        "database": "temporal",
        "visibilityHost": vis_host,
        "visibilityDatabase": "temporal_visibility",
    }
    postgres["configStore"] = {
        "host": (
            pg.config_store_host
            if (pg.enabled and pg.config_store_host)
            else "cluster-config-store-rw"
        ),
        "database": "config_store",
    }
    if pg.enabled and pg.dhcp_host:
        postgres["dhcp"] = {"host": pg.dhcp_host, "database": "kea_dhcp"}
    if pg.enabled and pg.nautobot_host:
        postgres["nautobot"] = {"host": pg.nautobot_host, "database": "nautobot"}
    return postgres


def _redis_metrics_enabled(config: NVConfigManagerInstallConfig) -> bool:
    """Return whether Redis metrics are effective for the bundled Redis."""
    monitoring = config.infrastructure.monitoring
    redis = config.external_services.redis
    return not (redis.enabled and redis.host) and (
        monitoring.redis_metrics_enabled or monitoring.observability_enabled
    )


def _build_external_services(config: NVConfigManagerInstallConfig) -> dict[str, Any]:
    """Build the ``externalServices`` section."""
    svc = config.services
    es = config.external_services
    ext: dict[str, Any] = {}

    if svc.nautobot:
        ext["nautobot"] = {"local": True, "localServer": "http://nautobot-nv-config-manager"}
    elif svc.external_nautobot_url:
        ext["nautobot"] = {"local": False, "server": svc.external_nautobot_url}
    elif config.dcim.provider == BUILT_IN_NAUTOBOT_PROVIDER and config.dcim.server:
        ext["nautobot"] = {"local": False, "server": config.dcim.server}
    elif config.dcim.provider != BUILT_IN_NAUTOBOT_PROVIDER:
        # Retain a disabled compatibility section for templates that still
        # carry Nautobot deployment settings but are not used by this provider.
        ext["nautobot"] = {"local": False}

    nats = es.nats
    if nats.enabled:
        ext["nats"] = {
            "server": nats.server,
            "authMethod": nats.auth_method.value,
            "local": False,
            "user": nats.user,
            "secretName": nats.secret_name,
            "externalSecretName": nats.external_secret_name,
            "credsPath": nats.creds_path,
        }
    else:
        ext["nats"] = {
            "server": "nats://nv-config-manager@nats:4222",
            "authMethod": "password",
            "local": True,
        }

    if es.redis.enabled and es.redis.host:
        r = es.redis
        ext["redis"] = {
            "local": False,
            "host": r.host,
            "port": r.port,
            "ssl": r.ssl,
            "passwordAuth": r.password_auth,
            "metricsExport": {"enabled": False},
        }
    else:
        ext["redis"] = {
            "local": True,
            "localHost": "redis-master",
            "metricsExport": {"enabled": _redis_metrics_enabled(config)},
        }

    ext["postgres"] = _build_postgres_section(es.postgres)

    if es.slack.channel:
        ext["slack"] = {"channel": es.slack.channel}

    return ext


def _build_dcim(config: NVConfigManagerInstallConfig) -> dict[str, Any]:
    """Build provider-neutral chart values from the installer configuration."""
    dcim = config.dcim
    section: dict[str, Any] = {
        "provider": dcim.provider,
        "tokenSecret": {
            "name": dcim.token_secret_name,
            "key": dcim.token_secret_key,
        },
    }
    if dcim.server:
        section["server"] = dcim.server
    if dcim.public_url:
        section["publicUrl"] = dcim.public_url
    if dcim.display_name:
        section["displayName"] = dcim.display_name
    if dcim.verify != "":
        section["verify"] = dcim.verify
    if dcim.cache_refresh_interval:
        section["cacheRefreshInterval"] = dcim.cache_refresh_interval
    if dcim.cache_ttl:
        section["cacheTtl"] = dcim.cache_ttl
    if dcim.event_stream or dcim.event_subject:
        section["events"] = {"stream": dcim.event_stream, "subject": dcim.event_subject}
    if dcim.options:
        section["options"] = dict(dcim.options)
    if dcim.provider_packages:
        section["providerPackages"] = {
            "enabled": True,
            "images": [
                {
                    "name": package.name,
                    "image": package.image,
                    "pullPolicy": package.pull_policy,
                }
                for package in dcim.provider_packages
            ],
        }
    return section


def _build_config_secrets(config: NVConfigManagerInstallConfig, values: dict[str, Any]) -> None:
    """Populate ``secrets.vault.configSecrets`` in *values*."""
    svc = config.services
    secrets_section = values.setdefault("secrets", {})
    vault_section = secrets_section.setdefault("vault", {})
    if svc.render and config.sites:
        if config.secrets.method == SecretsMethod.ESO:
            vault_section["configSecrets"] = build_eso_config_secrets(config)
        else:
            vault_section["configSecrets"] = {
                "enabled": True,
                "sites": [{"name": s.name} for s in config.sites],
            }
    else:
        vault_section.setdefault("configSecrets", {"enabled": False})


def _build_ztp_storage(config: NVConfigManagerInstallConfig) -> dict[str, Any]:
    """Build the ``networkZtp.storage`` sub-section from ZTP storage config."""
    zs = config.infrastructure.ztp_storage
    storage: dict[str, Any] = {"type": zs.type.value}
    if zs.type == ZTPStorageType.FILE:
        file_cfg: dict[str, Any] = {
            "pvcName": zs.pvc_name,
            "mountPath": "/mnt/images",
        }
        if zs.storage_class:
            file_cfg["storageClass"] = zs.storage_class
        if zs.pvc_size:
            file_cfg["size"] = zs.pvc_size
        storage["file"] = file_cfg
    else:
        s3_cfg: dict[str, Any] = {}
        if zs.s3_bucket:
            s3_cfg["bucketName"] = zs.s3_bucket
        if zs.s3_ceph.enabled:
            ceph_cfg: dict[str, Any] = {"enabled": True}
            if zs.s3_ceph.user_secret_name:
                ceph_cfg["userSecretName"] = zs.s3_ceph.user_secret_name

            user = zs.s3_ceph.object_store_user
            user_cfg: dict[str, Any] = {}
            if user.name != "ztp-user":
                user_cfg["name"] = user.name
            if user.store != "ceph-objectstore":
                user_cfg["store"] = user.store
            if user.cluster_namespace != "rook-ceph":
                user_cfg["clusterNamespace"] = user.cluster_namespace
            if user_cfg:
                ceph_cfg["objectStoreUser"] = user_cfg

            obc = zs.s3_ceph.object_bucket_claim
            obc_cfg: dict[str, Any] = {}
            if obc.storage_class_name:
                obc_cfg["storageClassName"] = obc.storage_class_name
            if obc.bucket_max_size and obc.bucket_max_size != "50G":
                obc_cfg["bucketMaxSize"] = obc.bucket_max_size
            if obc_cfg:
                ceph_cfg["objectBucketClaim"] = obc_cfg

            s3_cfg["ceph"] = ceph_cfg
        else:
            if zs.s3_endpoint:
                s3_cfg["endpoint"] = zs.s3_endpoint
            if zs.s3_region:
                s3_cfg["region"] = zs.s3_region
            if _has_ztp_s3_k8s_credentials(config):
                s3_cfg["credentialsSecret"] = _ZTP_S3_CREDENTIALS_SECRET_NAME
        if s3_cfg:
            storage["s3"] = s3_cfg
    return storage


def _build_lb_ingress(
    config: NVConfigManagerInstallConfig,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build ``networkZtp`` and ``networkDhcp`` sections."""
    svc = config.services
    lb = config.infrastructure.load_balancer
    ztp: dict[str, Any] = {"enabled": svc.ztp, "client": {"useInternalEndpoint": True}}
    ztp["storage"] = _build_ztp_storage(config)
    if config.infrastructure.ztp_storage.node_selector:
        ztp["nodeSelector"] = dict(config.infrastructure.ztp_storage.node_selector)
    dhcp: dict[str, Any] = {"enabled": svc.dhcp}

    if lb.provider == LBProvider.NLB:
        ztp_nlb = _build_nlb_service_values(lb.nlb_ztp)
        if ztp_nlb:
            ztp["ingress"] = {"nlb": ztp_nlb}
        dhcp_nlb = _build_nlb_service_values(lb.nlb_dhcp)
        if dhcp_nlb:
            dhcp["ingress"] = {"nlb": dhcp_nlb}
    elif lb.provider != LBProvider.NONE:
        _apply_static_lb(ztp, lb.ztp_lb_ip, lb.ztp_dns_name, lb)
        _apply_static_lb(dhcp, lb.dhcp_lb_ip, lb.dhcp_dns_name, lb)
    return ztp, dhcp


def _apply_static_lb(section: dict[str, Any], ip: str, dns: str, lb: Any) -> None:
    """Apply MetalLB/Cilium static IP LB config to a service section."""
    if not ip:
        return
    entry: dict[str, Any] = {"staticIP": ip}
    if dns:
        entry["hostname"] = dns
    if lb.allowed_prefixes:
        entry["allowedPrefixes"] = list(lb.allowed_prefixes)
    section["ingress"] = {lb.provider.value: entry}


def _build_nautobot(config: NVConfigManagerInstallConfig) -> dict[str, Any]:
    """Build the ``nautobot`` section."""
    svc = config.services
    section: dict[str, Any] = {"enabled": svc.nautobot}
    if not svc.nautobot:
        return section
    section.update(
        {
            "admin": {"username": "admin", "email": "admin@example.com"},
            "server": {"db": {"host": "cluster-nautobot-rw", "port": 5432, "name": "nautobot"}},
            "celery": {"enabled": True},
            "nginx": {"enabled": True},
            "initJob": {"enabled": True, "singleInit": True},
            "persistence": {"staticFiles": {"enabled": True, "size": "1Gi"}},
        }
    )
    if config.content.jobs:
        jobs_config = config.content.jobs_config
        section["customJobs"] = {
            "enabled": True,
            "createPvc": False,
            "pvcName": "nautobot-custom-jobs",
            "accessMode": jobs_config.access_mode or "ReadWriteOnce",
        }
        if jobs_config.storage_class:
            section["customJobs"]["storageClass"] = jobs_config.storage_class
        if jobs_config.node_selector:
            section["customJobs"]["nodeSelector"] = dict(jobs_config.node_selector)
    return section


def _build_cnpg(config: NVConfigManagerInstallConfig) -> dict[str, Any]:
    """Build the ``cnpg`` section."""
    svc = config.services
    # CNPG PodMonitors are chart-managed under monitoring.podMonitors.cnpg
    # (see templates/monitoring.yaml); do not emit cnpg.monitoring.
    section: dict[str, Any] = {
        "enabled": True,
    }
    backup_cfg = config.infrastructure.cnpg_s3_backup
    if backup_cfg.enabled:
        section["backup"] = {
            "destinationType": "s3",
            "s3": {
                "endpoint": backup_cfg.endpoint,
                "bucket": backup_cfg.bucket,
                "path": backup_cfg.path,
            },
            "credentialsSecret": "cnpg-backup-credentials",
        }

    def _cluster(enabled: bool) -> dict[str, Any]:
        cluster: dict[str, Any] = {"enabled": enabled, "enablePDB": False}
        if backup_cfg.enabled:
            cluster["backup"] = {
                "enabled": True,
                "schedule": "0 0 0 * * *",
                "retentionPolicy": "30d",
            }
        return cluster

    temporal_server_enabled = svc.temporal and not bool(config.external_services.temporal.address)
    section["temporal"] = _cluster(temporal_server_enabled)
    section["temporalVisibility"] = _cluster(temporal_server_enabled)
    section["configStore"] = _cluster(svc.config_store)
    section["dhcp"] = _cluster(svc.dhcp)
    section["nautobot"] = _cluster(svc.nautobot)
    return section


def _build_rbac(config: NVConfigManagerInstallConfig) -> dict[str, Any]:
    """Build the top-level ``rbac`` block for Temporal workflow authorization."""
    overrides = {o.name: o for o in config.rbac.workflow_overrides}
    workflows: list[dict[str, Any]] = []
    for name in get_known_workflows():
        if name in overrides:
            entry = overrides[name]
            workflows.append(
                {
                    "name": name,
                    "read_roles": list(entry.read_roles),
                    "execute_roles": list(entry.execute_roles),
                }
            )
        else:
            workflows.append(
                {
                    "name": name,
                    "read_roles": list(config.rbac.default_read_roles),
                    "execute_roles": list(config.rbac.default_execute_roles),
                }
            )
    return {"admin_roles": list(config.rbac.admin_roles), "workflows": workflows}


def build_values(
    config: NVConfigManagerInstallConfig,
    _secrets_state: dict[str, str],
    *,
    local_images: bool = False,
    local_tags: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build the complete Helm values dict from config and secrets."""
    svc = config.services
    is_local = local_images or config.images.source == ImageSource.LOCAL

    values: dict[str, Any] = {}
    values["global"] = _build_global(config, is_local=is_local, local_tags=local_tags)
    values.update(_build_secrets(config))

    git_tokens_list = _build_git_tokens(config)
    if git_tokens_list:
        secrets_section = values.setdefault("secrets", {})
        vault_section = secrets_section.setdefault("vault", {})
        paths_section = vault_section.setdefault("paths", {})
        paths_section["gitTokens"] = git_tokens_list

    values.update(_build_gateway(config))
    values["spiffe"] = _build_spiffe(config)
    values["networkPolicy"] = {
        "enabled": True,
        "gatewayNamespace": config.infrastructure.gateway_namespace
        or (
            "kgateway-system"
            if config.infrastructure.gateway.value == "kgateway"
            else "envoy-gateway-system"
        ),
    }

    _build_oidc(config, values)
    values["dcim"] = _build_dcim(config)
    values["externalServices"] = _build_external_services(config)

    render_section: dict[str, Any] = {
        "enabled": svc.render,
        "client": {"useInternalEndpoint": True},
    }
    tpc = config.content.template_plugins_config
    if config.content.template_plugins:
        render_section["templatePlugins"] = {
            "enabled": True,
            "pvcName": "render-service-template-plugins",
            "mountPath": "/opt/template-plugins",
            "images": [],
        }
    if tpc.node_selector:
        ns_val: dict[str, str] = dict(tpc.node_selector)
        render_section["api"] = {"nodeSelector": ns_val}
        render_section["consumers"] = {
            "nautobot": {"nodeSelector": ns_val},
            "device": {"nodeSelector": ns_val},
        }
        render_section["templateUpdater"] = {"nodeSelector": ns_val}
    values["renderService"] = render_section
    values["ui"] = {"enabled": True}

    _build_config_secrets(config, values)

    ztp, dhcp = _build_lb_ingress(config)
    values["networkZtp"] = ztp
    values["networkDhcp"] = dhcp

    external_temporal = config.external_services.temporal
    temporal_client: dict[str, Any] = {
        "useInternalEndpoint": True,
        "address": external_temporal.address,
        "namespace": external_temporal.namespace,
        "tls": {
            "enabled": external_temporal.auth_method.value == "mtls",
            "secretName": external_temporal.tls_secret_name,
            "serverName": external_temporal.tls_server_name,
        },
    }
    temporal_section: dict[str, Any] = {
        "enabled": svc.temporal,
        "server": {"enabled": not bool(external_temporal.address)},
        "client": temporal_client,
        "redfish": {"enabled": config.redfish.enabled},
    }
    values["temporal"] = temporal_section
    values["rbac"] = _build_rbac(config)
    values["configStore"] = {"enabled": svc.config_store, "client": {"useInternalEndpoint": True}}
    mcp_forward_resource = config.sso.provider != SSOProvider.AZURE
    values["mcp"] = {
        "enabled": svc.temporal and svc.config_store and svc.dhcp,
        "auth": {"oauth": {"forwardResourceParameter": mcp_forward_resource}},
    }
    values["nautobot"] = _build_nautobot(config)

    values["nautobotNats"] = {
        "enabled": not config.external_services.nats.enabled,
        "jetstream": {"enabled": True},
        "natsReady": {"enabled": True, "useNatsCli": True},
    }

    values["cnpg"] = _build_cnpg(config)

    monitoring = config.infrastructure.monitoring
    monitoring_active = monitoring.enabled or monitoring.observability_enabled
    monitoring_values: dict[str, Any] = {
        "podMonitors": {
            "redis": {"enabled": _redis_metrics_enabled(config) and monitoring_active},
        },
    }
    if monitoring_active:
        # The local-dev observability stack relies on the existing PodMonitor
        # resources in templates/monitoring.yaml to scrape nv-config-manager pods, so we
        # flip monitoring.enabled on whenever observability is on. The master
        # switch alone isn't enough — each PodMonitor and Probe in
        # templates/monitoring.yaml is also gated on
        # ``monitoring.podMonitors.enabled`` / ``monitoring.probes.enabled``
        # (both default false in values.yaml) so without these Alloy's
        # prometheus.operator.* informers run but find zero CRs to scrape and
        # Prometheus stays empty.
        prometheus_namespace = monitoring.prometheus_namespace
        if monitoring.observability_enabled:
            # Local Prometheus + Alloy run in the release namespace.
            prometheus_namespace = config.cluster.namespace
        monitoring_values.update(
            {
                "enabled": True,
                "probes": {"enabled": True},
                "prometheus": {"namespace": prometheus_namespace},
            }
        )
        monitoring_values["podMonitors"].update(
            {
                "enabled": True,
                # Chart CNPG PodMonitors also require monitoring.podMonitors.cnpg.enabled
                # (plus cnpg.enabled and each cluster's enabled flag).
                "cnpg": {"enabled": True},
            }
        )
    values["monitoring"] = monitoring_values

    if monitoring.observability_enabled:
        # Flip the subchart condition gates so prometheus / alloy render.
        # Full configuration lives in
        # deploy/helm/values-observability.yaml, which the deployer layers in
        # via an additional `-f` flag. Alloy itself watches the PodMonitor /
        # ServiceMonitor / Probe CRs (via its prometheus.operator.*
        # components) so we don't ship the Prometheus Operator pod. Grafana
        # and Loki are AGPL-licensed and are not enabled by default.
        #
        # prometheus-operator-crds is NOT a subchart -- it's installed as a
        # sibling Helm release by Deployer._install_crds before the main
        # chart, to avoid the helm validation deadlock where the parent
        # references PodMonitor before its CRD is registered. See the long
        # note in deploy/helm/Chart.yaml.
        values["prometheus"] = {"enabled": True}
        values["alloy"] = {"enabled": True}

    _apply_image_overrides(config, values)
    return values


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge Helm values using Helm-like override semantics."""
    result = deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _resolve_chart_dir(chart_dir: Path | str | None = None) -> Path | None:
    """Find the Helm chart directory when the repository is available."""
    if chart_dir is not None:
        candidate = Path(chart_dir)
        if (candidate / "values.yaml").exists():
            return candidate

    search_roots = [Path.cwd(), Path(__file__).resolve()]
    for start in search_roots:
        cur = start if start.is_dir() else start.parent
        for parent in (cur, *cur.parents):
            candidate = parent / "deploy" / "helm"
            if (candidate / "values.yaml").exists():
                return candidate
    return None


def _load_values_file(path: Path) -> dict[str, Any]:
    """Load a Helm values YAML file as a mapping."""
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        return {}
    return data


def build_complete_values(
    config: NVConfigManagerInstallConfig,
    secrets_state: dict[str, str],
    *,
    local_images: bool = False,
    local_tags: dict[str, str] | None = None,
    chart_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Build one override file with selected size profile and TUI-generated values."""
    generated = build_values(
        config,
        secrets_state,
        local_images=local_images,
        local_tags=local_tags,
    )

    resolved_chart_dir = _resolve_chart_dir(chart_dir)
    if resolved_chart_dir is None:
        return generated

    size_values = resolved_chart_dir / f"values-local-{config.cluster.size.value}.yaml"
    values: dict[str, Any] = {}
    if size_values.exists():
        values = _deep_merge(values, _load_values_file(size_values))

    return _deep_merge(values, generated)


def generate_helm_values(
    config: NVConfigManagerInstallConfig,
    secrets_state: dict[str, str],
    output_path: Path,
    *,
    local_images: bool = False,
    local_tags: dict[str, str] | None = None,
    chart_dir: Path | str | None = None,
    complete: bool = True,
) -> None:
    """Generate the Helm values YAML file."""
    if complete:
        values = build_complete_values(
            config,
            secrets_state,
            local_images=local_images,
            local_tags=local_tags,
            chart_dir=chart_dir,
        )
    else:
        values = build_values(
            config,
            secrets_state,
            local_images=local_images,
            local_tags=local_tags,
        )

    header = (
        f"# =============================================================================\n"
        f"# NVIDIA Config Manager - Generated Configuration\n"
        f"# =============================================================================\n"
        f"# Generated by nv-config-manager-installer on {datetime.now(tz=UTC).isoformat()}\n"
        f"# Environment: {config.cluster.environment}\n"
        f"# Base Hostname: {config.cluster.hostname}\n"
        f"# Secrets Method: {config.secrets.method.value}\n"
        f"# Resource Size: {config.cluster.size.value}\n"
        f"# Contains: generated overrides plus selected size profile; chart defaults are not embedded\n"
        f"# =============================================================================\n\n"
    )

    body = yaml.dump(values, default_flow_style=False, sort_keys=False)
    output_path.write_text(header + body)
