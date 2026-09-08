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
"""Custom vault filters."""

import configparser
import os
import pathlib

import hvac
from passlib.hash import cisco_type7, md5_crypt, sha512_crypt

from nv_config_manager_templates.filters import FilterException


def _hvac_client():
    """Return an appropriate Hashicorp Vault client for this environment."""
    # TODO: Detect AWS and extract token from environment
    token_path = pathlib.Path("~/.vault-token").expanduser()
    with open(token_path, encoding="utf-8") as f:
        token = f.read()
    return hvac.Client(
        url="https://prod.vault.nvidia.com", verify=True, namespace="ngc", token=token
    )


def load_secret(value: str, region: str = None, site: str = None) -> str:
    """Load a secret value from a given path."""
    site_slug = site.replace(" ", "-").lower() if site else None
    region_slug = region.replace(" ", "-").lower() if region else None
    if region:
        path = f"nvdc-net/kiwi/region/{region_slug}/config_secrets"
    elif site:
        path = f"nvdc-net/kiwi/site/{site_slug}/config_secrets"
    else:
        raise FilterException("Must supply site or region for key lookup.")

    # Return the path/key if we're skipping vault lookups
    # this is useful for local renders and unit testing
    if os.getenv("NV_CONFIG_MANAGER_SKIP_VAULT") == "1":
        return f"{path}:{value}"

    # If we're running in a K8s environment with vault agent injector
    # the secrets will be synced to a file by the injector
    if os.getenv("NV_CONFIG_MANAGER_CONFIG_SECRET_PATH"):
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(os.getenv("NV_CONFIG_MANAGER_CONFIG_SECRET_PATH"))
        if region:
            section = f"region.{region_slug}"
        elif site:
            section = f"site.{site_slug}"
        try:
            return parser[section][value]
        except KeyError as exc:
            raise FilterException(
                f"Secret {value} not found for {region if region else site}."
            ) from exc

    client = _hvac_client()
    rsp = client.secrets.kv.read_secret_version(path=path, mount_point="secrets")
    try:
        return rsp["data"]["data"][value]
    except KeyError as exc:
        raise FilterException(
            f"Secret {value} not found for {region if region else site}."
        ) from exc


def encrypt(value: str, algo: str, site: str | None = None) -> str:
    """Encrypt a secret using the given algorithm.

    Args:
        value: The secret value to encrypt
        algo: The algorithm to use (sha512, md5, ciscot7)
        site: Optional site name to use for salt lookup
    """
    # Load salt from Vault if site is provided
    salt = None
    if site:
        # If skipping vault, use static salt for testing
        if os.getenv("NV_CONFIG_MANAGER_SKIP_VAULT") == "1":
            salt = "0" if algo == "ciscot7" else "H0QFj2rx"
        else:
            if algo == "ciscot7":
                salt = load_secret("hash_salt_t7", site=site)
            else:
                salt = load_secret("hash_salt", site=site)

    if algo == "sha512":
        # Including fixed rounds of 5000 so that its excluded from
        # the final hash string
        return sha512_crypt.using(rounds=5000, salt=salt).hash(value)
    if algo == "md5":
        return md5_crypt.using(salt=salt).hash(value)
    if algo == "ciscot7":
        # Drop support for this if Arista ever allows
        # anything better for tacacs key hashed value
        try:
            salt_int = int(salt) if salt else None
        except ValueError as exc:
            raise FilterException(f"Invalid ciscot7 salt value: {salt}") from exc
        return cisco_type7.using(salt=salt_int).hash(value)

    raise FilterException(f"No encryption implementation present for {algo}.")
