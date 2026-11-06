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
"""Secret generation and ESO config building.

Generates installer-managed secrets and passwords for deployments.
For network secrets with source=generate, creates random passwords.
For source=manual, the user must provide values at deploy time (placeholder used).
For source=vault, the value comes from ESO at runtime and is not stored here.
"""

from __future__ import annotations

import json
import secrets
import string
from typing import Any

from nv_config_manager_installer.providers.api import DCIMInstallerProvider
from nv_config_manager_installer.providers.registry import resolve_provider
from nv_config_manager_installer.schema import (
    NVConfigManagerInstallConfig,
    PasswordSource,
    SecretsMethod,
    VaultAuthMethod,
    VaultPathConfig,
    VaultPathsConfig,
    ZTPStorageType,
    database_secret_keys,
)


def _k8s_val(config: NVConfigManagerInstallConfig, group: str, vault_key: str) -> str:
    """Return a manually supplied k8s secret value, or empty string if not set."""
    grp = config.secrets.k8s.group(group)
    if not grp.enabled:
        return ""
    return grp.values.get(vault_key, "")


_CRYPT_SAFE_CHARS = string.ascii_letters + string.digits + "./"
_URL_SAFE_CHARS = string.ascii_letters + string.digits + "-_~"
_NATS_CONFIG_PASSWORD_FIRST_CHARS = string.ascii_letters
_NATS_CONFIG_PASSWORD_CHARS = string.ascii_letters + string.digits
_NATS_CONFIG_PASSWORD_MIN_LENGTH = 16
REQUIRED_SITE_SECRET_KEYS = ("root_password_r1", "api_user_key_r1")


def _validate_nats_config_password(password: str) -> str:
    """Validate a password used in unquoted NATS config variable expansion."""
    if len(password) < _NATS_CONFIG_PASSWORD_MIN_LENGTH:
        raise ValueError(f"natsPassword must be at least {_NATS_CONFIG_PASSWORD_MIN_LENGTH} chars")
    if password[0] not in _NATS_CONFIG_PASSWORD_FIRST_CHARS or any(
        char not in _NATS_CONFIG_PASSWORD_CHARS for char in password[1:]
    ):
        raise ValueError("natsPassword must match [A-Za-z][A-Za-z0-9]*")
    return password


def _generate_password(length: int = 32) -> str:
    """Generate a random password using only sha512_crypt-safe characters [a-zA-Z0-9./]."""
    return "".join(secrets.choice(_CRYPT_SAFE_CHARS) for _ in range(length))


def _generate_url_safe_password(length: int = 32) -> str:
    """Generate a random password safe for use in database/service connection string URLs."""
    return "".join(secrets.choice(_URL_SAFE_CHARS) for _ in range(length))


def _generate_nats_config_password(length: int = 32) -> str:
    """Generate a password safe for unquoted NATS config variable expansion."""
    if length < _NATS_CONFIG_PASSWORD_MIN_LENGTH:
        raise ValueError(f"length must be at least {_NATS_CONFIG_PASSWORD_MIN_LENGTH}")
    first = secrets.choice(_NATS_CONFIG_PASSWORD_FIRST_CHARS)
    rest = "".join(secrets.choice(_NATS_CONFIG_PASSWORD_CHARS) for _ in range(length - 1))
    return _validate_nats_config_password(f"{first}{rest}")


def _generate_token(length: int = 40) -> str:
    """Generate a random hex token."""
    return secrets.token_hex((length + 1) // 2)[:length]


def _generate_redfish_secrets(config: NVConfigManagerInstallConfig, state: dict[str, str]) -> None:
    """Populate Redfish/BMC credential secrets when explicitly enabled."""
    if not config.redfish.enabled or config.secrets.method != SecretsMethod.KUBERNETES:
        return
    for vendor, creds in config.redfish.vendors.items():
        state[f"redfish_{vendor}_default_user"] = creds.default_user or "local-mock-user"
        state[f"redfish_{vendor}_default_password"] = creds.default_password or _generate_password()
        state[f"redfish_{vendor}_config_manager_password"] = (
            creds.config_manager_password or _generate_password()
        )


_DB_GROUPS: tuple[str, ...] = ("temporal", "temporal_visibility", "config_store", "dhcp")


def application_databases(provider: DCIMInstallerProvider) -> tuple[str, ...]:
    """Return core CNPG application databases plus the provider's."""
    return (*_DB_GROUPS, *provider.secrets.databases)


def _generate_core_k8s_secrets(
    config: NVConfigManagerInstallConfig,
    state: dict[str, str],
    _v: Any,
    provider: DCIMInstallerProvider,
) -> None:
    """Populate core Kubernetes secrets for the selected DCIM and services.

    Explicit core values are taken first, the provider then seeds values from
    its own groups, and anything still missing is generated.
    """
    if token := _v("dcim", "token"):
        state["dcim_token"] = token
    if nats_password := _v("nats", "password"):
        state["nats_password"] = nats_password
    provider.generate_secret_state(config, state, _v)

    state.setdefault("dcim_token", _generate_token(40))
    state["nats_password"] = _validate_nats_config_password(
        state.get("nats_password") or _generate_nats_config_password()
    )
    state["redis_password"] = _v("redis", "password") or _generate_url_safe_password()
    for db in application_databases(provider):
        user_key, pass_key = database_secret_keys(db)
        state[f"{db}_db_user"] = _v("postgres", user_key) or db
        state[f"{db}_db_password"] = _v("postgres", pass_key) or _generate_url_safe_password()


def _generate_optional_k8s_secrets(
    config: NVConfigManagerInstallConfig, state: dict[str, str], _v: Any
) -> None:
    """Populate optional integration secrets (Slack, Jira, CNPG backup)."""
    k8s = config.secrets.k8s
    if config.sso.enabled:
        state["oidc_cookie_secret"] = _generate_token(32)
    if k8s.slack.enabled:
        state["slack_token"] = _v("slack", "token") or _generate_url_safe_password()
    if k8s.jira.enabled:
        state["jira_base_url"] = _v("jira", "baseUrl") or ""
        state["jira_api_token"] = _v("jira", "apiToken") or ""
    if k8s.cnpg_backup.enabled:
        state["cnpg_access_key_id"] = _v("cnpg_backup", "accessKeyId") or ""
        state["cnpg_access_secret_key"] = _v("cnpg_backup", "accessSecretKey") or ""
    ztp_storage = config.infrastructure.ztp_storage
    if (
        ztp_storage.type == ZTPStorageType.S3
        and not ztp_storage.s3_ceph.enabled
        and k8s.ztp_s3.enabled
    ):
        state["ztp_s3_endpoint"] = _v("ztp_s3", "endpoint") or ""
        state["ztp_s3_access_key_id"] = _v("ztp_s3", "accessKeyId") or ""
        state["ztp_s3_secret_access_key"] = _v("ztp_s3", "secretAccessKey") or ""


def generate_secrets(
    config: NVConfigManagerInstallConfig,
    provider: DCIMInstallerProvider | None = None,
) -> dict[str, str]:
    """Generate all secrets needed for deployment.

    Returns a dict of key -> value for every secret that needs a concrete value.
    Vault-sourced secrets are omitted (ESO provides them at runtime).

    The dict includes:
    - Network secrets:    {secret_key}_{rotation} -> generated or manually supplied value
                          (manual entries must have a non-empty value; missing values raise ValueError)
    - Infrastructure:     dcim_token, redis_password, nats_password, hash_salt,
                          database credentials, and provider-owned values.
    """
    provider = provider or resolve_provider(config)
    state: dict[str, str] = {}
    configured_network_keys: set[str] = set()

    # -- Network secrets --
    for entry in config.network_secrets:
        if not entry.secret_key:
            continue
        full_key = (
            entry.secret_key if not entry.rotation else f"{entry.secret_key}_{entry.rotation}"
        )
        configured_network_keys.add(full_key)
        if entry.source == PasswordSource.GENERATE:
            state[full_key] = _generate_password()
        elif entry.source == PasswordSource.MANUAL:
            if not entry.value.strip():
                raise ValueError(
                    f"Manual secret '{full_key}' has no value; "
                    "manual entries must be provided before deployment."
                )
            state[full_key] = entry.value

    # -- Hash salt --
    state["hash_salt"] = _generate_password(8)

    # These values are used by every NVCM site.  The TUI normally creates
    # their entries, but keep non-interactive ESO runs safe as well.  Do not
    # generate over an explicitly Vault-sourced entry: the OpenBao populator
    # verifies that it already exists at the site's configured path.
    for key in REQUIRED_SITE_SECRET_KEYS:
        if key not in configured_network_keys:
            state[key] = _generate_password()

    if config.secrets.method != SecretsMethod.KUBERNETES:
        return state

    def _v(group: str, vault_key: str) -> str:
        return _k8s_val(config, group, vault_key)

    _generate_core_k8s_secrets(config, state, _v, provider)
    _generate_optional_k8s_secrets(config, state, _v)
    _generate_redfish_secrets(config, state)

    return state


def build_openbao_secret_data(
    config: NVConfigManagerInstallConfig,
    provider: DCIMInstallerProvider | None = None,
) -> dict[str, dict[str, str]]:
    """Build logical OpenBao values for every installer-supported ESO path.

    The returned keys are schema path-group names and logical Helm key names.
    The OpenBao writer translates logical names through each group's configured
    ``keys`` mapping before writing KV v2 data. Disabled groups are skipped by
    the writer, so the generic ``dcim`` and ``nats`` groups are always built.
    """
    provider = provider or resolve_provider(config)

    def value(group: str, key: str) -> str:
        return _k8s_val(config, group, key)

    groups: dict[str, dict[str, str]] = {
        "redis": {
            "password": value("redis", "password") or _generate_url_safe_password(),
        },
        "postgres": {},
        "network": {
            "user": value("network", "user") or config.secrets.config_manager_service_username,
            "password": value("network", "password") or _generate_url_safe_password(),
        },
        "dcim": {"token": value("dcim", "token") or _generate_token(40)},
        "nats": {"password": value("nats", "password") or _generate_nats_config_password()},
    }
    groups.update(provider.openbao_groups(config, value))
    if not config.dcim.local:
        for spec in provider.secrets.groups:
            if spec.local_only:
                groups.pop(spec.name, None)
    for database in application_databases(provider):
        user_key, password_key = database_secret_keys(database)
        groups["postgres"][user_key] = value("postgres", user_key) or database
        groups["postgres"][password_key] = value("postgres", password_key) or (
            _generate_url_safe_password()
        )

    if config.sso.enabled:
        groups["oidc"] = {
            "clientSecret": config.sso.client_secret,
            "cookieSecret": _generate_url_safe_password(),
        }

    optional = (
        ("slack", {"token": value("slack", "token")}),
        (
            "jira",
            {
                "baseUrl": value("jira", "baseUrl"),
                "apiToken": value("jira", "apiToken"),
            },
        ),
        (
            "cnpg_backup",
            {
                "accessKeyId": value("cnpg_backup", "accessKeyId"),
                "accessSecretKey": value("cnpg_backup", "accessSecretKey"),
            },
        ),
        (
            "ztp_s3",
            {
                "endpoint": value("ztp_s3", "endpoint"),
                "accessKeyId": value("ztp_s3", "accessKeyId"),
                "secretAccessKey": value("ztp_s3", "secretAccessKey"),
            },
        ),
    )
    for group, data in optional:
        if group == "ztp_s3" and (
            config.infrastructure.ztp_storage.type != ZTPStorageType.S3
            or config.infrastructure.ztp_storage.s3_ceph.enabled
        ):
            continue
        if config.secrets.vault.paths.group(group).enabled:
            groups[group] = data

    if config.redfish.enabled:
        redfish: dict[str, str] = {}
        for vendor in ("lenovo", "bluefield"):
            creds = config.redfish.vendors.get(vendor)
            default_user = creds.default_user if creds else ""
            default_password = creds.default_password if creds else ""
            manager_password = creds.config_manager_password if creds else ""
            redfish[f"{vendor}DefaultUser"] = default_user or (
                "USERID" if vendor == "lenovo" else "admin"
            )
            redfish[f"{vendor}DefaultPassword"] = default_password or _generate_password()
            redfish[f"{vendor}ConfigManagerPassword"] = manager_password or _generate_password()
        groups["redfish"] = redfish
        default_creds = config.redfish.vendors.get("default")
        groups["bmc"] = {
            "credsJson": json.dumps(
                {
                    "default": {
                        "username": (
                            default_creds.default_user
                            if default_creds and default_creds.default_user
                            else "admin"
                        ),
                        "password": (
                            default_creds.default_password
                            if default_creds and default_creds.default_password
                            else _generate_password()
                        ),
                    }
                }
            )
        }

    return groups


# ---------------------------------------------------------------------------
# ESO config generation
# ---------------------------------------------------------------------------

# Maps schema field names (snake_case) to the camelCase keys the Helm chart
# expects under ``secrets.vault.paths``.  Also defines the fallback path
# suffix used when the user hasn't specified a custom vault path.
_VAULT_PATH_GROUPS: list[tuple[str, str, str]] = [
    # (schema_field, helm_key, default_path_suffix)
    ("dcim", "dcim", "dcim"),
    ("nats", "nats", "nats"),
    ("redis", "redis", "redis"),
    ("postgres", "postgres", "postgres"),
    ("network", "network", "network"),
    ("oidc", "oidc", "oidc"),
    ("redfish", "redfish", "redfish"),
    ("bmc", "bmc", "bmc"),
    ("slack", "slack", "slack"),
    ("jira", "jira", "jira"),
    ("cnpg_backup", "cnpgBackup", "cnpg-backup"),
    ("ztp_s3", "ztpS3", "ztp-s3"),
]


def vault_path_groups(
    provider: DCIMInstallerProvider,
) -> list[tuple[str, str, str, dict[str, str]]]:
    """Return ``(schema_field, helm_key, path_suffix, default_keys)`` for core and provider groups.

    The ``postgres`` defaults include the provider's application databases.
    """
    defaults = VaultPathsConfig()
    groups: list[tuple[str, str, str, dict[str, str]]] = []
    for schema_field, helm_key, suffix in _VAULT_PATH_GROUPS:
        keys = dict(getattr(defaults, schema_field).keys)
        if schema_field == "postgres":
            for database in provider.secrets.databases:
                keys.update(database_secret_keys(database))
        groups.append((schema_field, helm_key, suffix, keys))
    for spec in provider.secrets.groups:
        groups.append((spec.name, spec.helm_key, spec.path_suffix, dict(spec.keys)))
    return groups


def _build_vault_auth(v: Any, vault_section: dict[str, Any]) -> None:
    """Populate auth-related fields on the vault section."""
    if v.auth.method == VaultAuthMethod.TOKEN:
        vault_section["tokenAuth"] = {"enabled": True, "secretName": v.auth.token_secret_name}
    else:
        if v.mount_path:
            vault_section["mountPath"] = v.mount_path
        if v.role:
            vault_section["role"] = v.role


def _build_vault_paths(v: Any, env: str, provider: DCIMInstallerProvider) -> dict[str, Any]:
    """Build the ``paths`` sub-section from the vault path groups."""
    paths: dict[str, Any] = {}
    for schema_field, helm_key, default_suffix, default_keys in vault_path_groups(provider):
        pc: VaultPathConfig = v.paths.group(schema_field)
        if not pc.enabled:
            continue
        entry: dict[str, Any] = {"path": pc.path or f"{env}/{default_suffix}"}
        keys = {**default_keys, **pc.keys}
        if keys:
            entry["keys"] = dict(keys)
        paths[helm_key] = entry
    return paths


def build_eso_vault_config(
    config: NVConfigManagerInstallConfig,
    provider: DCIMInstallerProvider | None = None,
) -> dict[str, Any]:
    """Build the ``secrets`` section for Helm values when using ESO."""
    if config.secrets.method != SecretsMethod.ESO:
        return {}

    provider = provider or resolve_provider(config)
    v = config.secrets.vault
    vault_section: dict[str, Any] = {
        "server": v.server,
        "namespace": v.namespace,
        "secretsPath": v.secrets_path,
    }
    if v.config_secrets_path:
        vault_section["configSecretsPath"] = v.config_secrets_path

    _build_vault_auth(v, vault_section)
    vault_section["paths"] = _build_vault_paths(v, config.cluster.environment, provider)

    return {"secrets": {"method": "eso", "vault": vault_section}}
