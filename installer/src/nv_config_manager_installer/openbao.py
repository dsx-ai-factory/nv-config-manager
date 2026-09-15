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
"""OpenBao KV v2 provisioning for installer-managed secrets."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import requests

from nv_config_manager_installer.accounts import build_eso_config_secrets
from nv_config_manager_installer.schema import (
    BUILT_IN_NAUTOBOT_PROVIDER,
    NVConfigManagerInstallConfig,
    PasswordSource,
    VaultPathsConfig,
)
from nv_config_manager_installer.secrets import (
    REQUIRED_SITE_SECRET_KEYS,
    build_openbao_secret_data,
    generate_secrets,
)

_GROUP_PATH_DEFAULTS = {
    "dcim": "dcim",
    "nats": "nats",
    "nautobot": "nautobot",
    "redis": "redis",
    "postgres": "postgres",
    "network": "network",
    "nautobot_app": "nautobot-app",
    "oidc": "oidc",
    "redfish": "redfish",
    "bmc": "bmc",
    "slack": "slack",
    "jira": "jira",
    "cnpg_backup": "cnpg-backup",
    "ztp_s3": "ztp-s3",
}


class OpenBaoError(RuntimeError):
    """Raised when an OpenBao API operation fails."""


@dataclass
class OpenBaoPopulationResult:
    """Summary of mounts and secret keys created during population."""

    mounts_created: list[str] = field(default_factory=list)
    paths_updated: list[str] = field(default_factory=list)
    keys_added: int = 0


class OpenBaoClient:
    """Small authenticated client for the OpenBao KV v2 HTTP API."""

    def __init__(
        self,
        server: str,
        token: str,
        *,
        namespace: str = "",
        timeout: int = 30,
        session: requests.Session | None = None,
    ) -> None:
        self.server = server.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({"X-Vault-Token": token})
        if namespace:
            self.session.headers.update({"X-Vault-Namespace": namespace})

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        allow_not_found: bool = False,
    ) -> requests.Response | None:
        url = f"{self.server}/v1/{path.lstrip('/')}"
        try:
            response = self.session.request(
                method,
                url,
                json=json_body,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise OpenBaoError(f"Vault request failed for {url}: {exc}") from exc
        if not response.ok:
            try:
                detail = "; ".join(response.json().get("errors", []))
            except (ValueError, AttributeError):
                detail = response.text.strip()
            missing_mount = (
                response.status_code == 400 and "no secret engine mount at" in detail.lower()
            )
            if allow_not_found and (response.status_code == 404 or missing_mount):
                return None
            suffix = f": {detail}" if detail else ""
            raise OpenBaoError(
                f"Vault {method} {path} returned HTTP {response.status_code}{suffix}"
            )
        return response

    def ensure_kv2_mount(self, mount: str) -> bool:
        """Ensure *mount* exists as a KV v2 secrets engine."""
        mount = mount.strip("/")
        encoded = quote(mount, safe="/")
        response = self._request("GET", f"sys/mounts/{encoded}", allow_not_found=True)
        if response is None:
            self._request(
                "POST",
                f"sys/mounts/{encoded}",
                json_body={"type": "kv", "options": {"version": "2"}},
            )
            return True
        payload = response.json()
        options = payload.get("options", {})
        if payload.get("type") != "kv" or str(options.get("version", "1")) != "2":
            raise OpenBaoError(f"Vault mount '{mount}' exists but is not KV v2")
        return False

    def read_secret(self, mount: str, path: str) -> tuple[dict[str, Any], int]:
        """Read KV v2 data and version, returning an empty version-zero record if absent."""
        endpoint = self._secret_endpoint(mount, path)
        response = self._request("GET", endpoint, allow_not_found=True)
        if response is None:
            return {}, 0
        payload = response.json().get("data", {})
        data = payload.get("data", {})
        metadata = payload.get("metadata", {})
        if not isinstance(data, dict):
            raise OpenBaoError(f"Vault secret '{mount}/{path}' returned invalid KV v2 data")
        return {str(key): value for key, value in data.items()}, int(metadata.get("version", 0))

    def write_missing(self, mount: str, path: str, desired: dict[str, Any]) -> list[str]:
        """Add absent keys without changing values already stored at *path*."""
        existing, version = self.read_secret(mount, path)
        additions = {key: value for key, value in desired.items() if key not in existing and value}
        if not additions:
            return []
        merged = {**existing, **additions}
        self._request(
            "POST",
            self._secret_endpoint(mount, path),
            json_body={"data": merged, "options": {"cas": version}},
        )
        return sorted(additions)

    @staticmethod
    def _secret_endpoint(mount: str, path: str) -> str:
        mount_part = quote(mount.strip("/"), safe="/")
        path_part = quote(path.strip("/"), safe="/")
        return f"{mount_part}/data/{path_part}"


class OpenBaoPopulator:
    """Populate all ESO paths described by an installer configuration."""

    def __init__(self, config: NVConfigManagerInstallConfig, client: OpenBaoClient) -> None:
        self.config = config
        self.client = client

    def populate(self) -> OpenBaoPopulationResult:
        result = OpenBaoPopulationResult()
        vault = self.config.secrets.vault
        main_mount = vault.secrets_path.strip("/")
        config_mount = (vault.config_secrets_path or main_mount).strip("/")
        mounts = {main_mount}
        if self.config.sites:
            mounts.add(config_mount)
        for mount in sorted(mounts):
            if self.client.ensure_kv2_mount(mount):
                result.mounts_created.append(mount)

        self._populate_application_paths(main_mount, result)
        self._populate_git_tokens(main_mount, result)
        self._populate_site_paths(config_mount, result)
        return result

    def _populate_application_paths(
        self,
        mount: str,
        result: OpenBaoPopulationResult,
    ) -> None:
        desired_groups = build_openbao_secret_data(self.config)
        defaults = VaultPathsConfig()
        environment = self.config.cluster.environment
        if self.config.dcim.provider == BUILT_IN_NAUTOBOT_PROVIDER:
            self._align_nautobot_tokens(mount, desired_groups, defaults, environment)
        for group, desired in desired_groups.items():
            path_config = getattr(self.config.secrets.vault.paths, group)
            if not path_config.enabled:
                continue
            default_keys = getattr(defaults, group).keys
            key_mapping = {**default_keys, **path_config.keys}
            translated = {
                key_mapping.get(logical_name, logical_name): value
                for logical_name, value in desired.items()
            }
            path = path_config.path or f"{environment}/{_GROUP_PATH_DEFAULTS[group]}"
            self._write_path(mount, path, translated, result)
            existing, _ = self.client.read_secret(mount, path)
            missing = [key for key in translated if not existing.get(key)]
            if missing:
                raise OpenBaoError(
                    f"Vault secret '{mount}/{path}' is missing required values: "
                    + ", ".join(sorted(missing))
                )

    def _align_nautobot_tokens(
        self,
        mount: str,
        desired_groups: dict[str, dict[str, str]],
        defaults: VaultPathsConfig,
        environment: str,
    ) -> None:
        """Keep the client token identical to Nautobot's superuser API token."""
        locations = [("nautobot", "token")]
        if "nautobot_app" in desired_groups:
            locations.append(("nautobot_app", "superuserApiToken"))
        existing_tokens: list[str] = []
        for group, logical_key in locations:
            path_config = getattr(self.config.secrets.vault.paths, group)
            if not path_config.enabled:
                continue
            key_mapping = {**getattr(defaults, group).keys, **path_config.keys}
            path = path_config.path or f"{environment}/{_GROUP_PATH_DEFAULTS[group]}"
            existing, _ = self.client.read_secret(mount, path)
            if token := existing.get(key_mapping.get(logical_key, logical_key)):
                existing_tokens.append(str(token))

        if len(set(existing_tokens)) > 1:
            raise OpenBaoError(
                "Vault Nautobot client token and superuser API token differ; "
                "rotate them to the same value before running the installer"
            )
        token = existing_tokens[0] if existing_tokens else desired_groups["nautobot"]["token"]
        desired_groups["nautobot"]["token"] = token
        if "nautobot_app" in desired_groups:
            desired_groups["nautobot_app"]["superuserApiToken"] = token

    def _populate_git_tokens(self, mount: str, result: OpenBaoPopulationResult) -> None:
        for token in self.config.git_tokens:
            if not token.vault_path:
                continue
            desired = {"token": token.token, "username": token.username}
            self._write_path(mount, token.vault_path, desired, result)
            existing, _ = self.client.read_secret(mount, token.vault_path)
            if not existing.get("token"):
                raise OpenBaoError(
                    f"Git token '{token.name}' has no configured value and is absent from "
                    f"Vault path '{mount}/{token.vault_path}'"
                )

    def _populate_site_paths(self, mount: str, result: OpenBaoPopulationResult) -> None:
        config_secrets = build_eso_config_secrets(self.config)
        generated = generate_secrets(self.config)
        desired = {
            key: value
            for key, value in generated.items()
            if key == "hash_salt"
            or key in REQUIRED_SITE_SECRET_KEYS
            or any(
                entry.secret_key
                and key
                == (
                    entry.secret_key
                    if not entry.rotation
                    else f"{entry.secret_key}_{entry.rotation}"
                )
                for entry in self.config.network_secrets
            )
        }
        for site in config_secrets.get("sites", []):
            path = str(site["path"])
            self._write_path(mount, path, desired, result)
            existing, _ = self.client.read_secret(mount, path)
            missing_vault_keys = []
            for entry in self.config.network_secrets:
                if entry.source != PasswordSource.VAULT or not entry.secret_key:
                    continue
                key = (
                    entry.secret_key
                    if not entry.rotation
                    else f"{entry.secret_key}_{entry.rotation}"
                )
                if not existing.get(key):
                    missing_vault_keys.append(key)
            if missing_vault_keys:
                raise OpenBaoError(
                    f"Vault site secret '{mount}/{path}' is missing Vault-sourced values: "
                    + ", ".join(sorted(missing_vault_keys))
                )

    def _write_path(
        self,
        mount: str,
        path: str,
        desired: dict[str, str],
        result: OpenBaoPopulationResult,
    ) -> None:
        added = self.client.write_missing(mount, path, desired)
        if added:
            result.paths_updated.append(f"{mount}/{path}")
            result.keys_added += len(added)
