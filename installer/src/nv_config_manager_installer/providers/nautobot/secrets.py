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
"""Secret groups and generation for the bundled Nautobot provider."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from nv_config_manager_installer.openbao import OpenBaoError
from nv_config_manager_installer.providers.api import (
    SecretGroupSpec,
    SecretsContribution,
    SecretValue,
)
from nv_config_manager_installer.schema import NVConfigManagerInstallConfig
from nv_config_manager_installer.secrets import (
    _generate_password,
    _generate_token,
)

_LABEL_API_TOKEN = "API Token"

SECRETS = SecretsContribution(
    groups=(
        SecretGroupSpec(
            name="nautobot",
            helm_key="nautobot",
            path_suffix="nautobot",
            label="Nautobot",
            keys={
                "token": "token",
                "readOnlyToken": "read_only_token",
                "natsPassword": "nats_password",
                "natsSysPassword": "nats_sys_password",
                "natsNautobotPassword": "nats_nautobot_password",
            },
            fields=(
                ("token", _LABEL_API_TOKEN),
                ("readOnlyToken", "Read-only API Token"),
                ("natsPassword", "NATS Password"),
                ("natsSysPassword", "NATS System Password"),
                ("natsNautobotPassword", "NATS Nautobot Password"),
            ),
        ),
        SecretGroupSpec(
            name="nautobot_app",
            helm_key="nautobotApp",
            path_suffix="nautobot-app",
            label="Nautobot App (admin/django)",
            keys={
                "adminPassword": "admin_password",
                "djangoSecretKey": "django_secret_key",
                "superuserApiToken": "superuser_api_token",
            },
            fields=(
                ("adminPassword", "Admin Password"),
                ("djangoSecretKey", "Django Secret Key"),
                ("superuserApiToken", "Superuser API Token"),
            ),
            local_only=True,
        ),
    ),
    databases=("nautobot",),
    nats_secret_names=("nats-nautobot",),
)


def generate_secret_state(
    config: NVConfigManagerInstallConfig,
    state: dict[str, str],
    value_of: SecretValue,
) -> None:
    """Seed the shared DCIM token and NATS password from the Nautobot groups."""
    if "dcim_token" not in state and (token := value_of("nautobot", "token")):
        state["dcim_token"] = token
    if "nats_password" not in state and (nats_password := value_of("nautobot", "natsPassword")):
        state["nats_password"] = nats_password
    if ro_token := value_of("nautobot", "readOnlyToken"):
        state["nautobot_read_only_token"] = ro_token
    if config.dcim.local:
        state["nautobot_admin_password"] = (
            value_of("nautobot_app", "adminPassword") or _generate_password()
        )
        state["django_secret_key"] = value_of("nautobot_app", "djangoSecretKey") or (
            _generate_password(50)
        )
        if superuser_token := value_of("nautobot_app", "superuserApiToken"):
            state["superuser_api_token"] = superuser_token


def token_secret_data(state: Mapping[str, str]) -> dict[str, str]:
    """Add the optional read-only token to the DCIM token Secret."""
    if ro_token := state.get("nautobot_read_only_token"):
        return {"read-only-token": ro_token}
    return {}


def openbao_groups(
    config: NVConfigManagerInstallConfig,
    value_of: SecretValue,
    *,
    nats_password: Callable[[], str],
    validate_nats_password: Callable[[str], str],
) -> dict[str, dict[str, str]]:
    """Return the ``nautobot`` and ``nautobot_app`` OpenBao groups."""
    token = (
        value_of("nautobot", "token")
        or value_of("nautobot_app", "superuserApiToken")
        or _generate_token(40)
    )
    groups: dict[str, dict[str, str]] = {
        "nautobot": {
            "token": token,
            "readOnlyToken": value_of("nautobot", "readOnlyToken") or _generate_token(40),
            "natsPassword": validate_nats_password(
                value_of("nautobot", "natsPassword") or nats_password()
            ),
            "natsSysPassword": value_of("nautobot", "natsSysPassword") or nats_password(),
            "natsNautobotPassword": value_of("nautobot", "natsNautobotPassword") or nats_password(),
        }
    }
    if config.dcim.local:
        groups["nautobot_app"] = {
            "adminPassword": value_of("nautobot_app", "adminPassword") or _generate_password(),
            "djangoSecretKey": value_of("nautobot_app", "djangoSecretKey")
            or _generate_password(50),
            "superuserApiToken": token,
        }
    return groups


def align_openbao_tokens(
    read_group: Callable[[str], dict[str, str]],
    desired_groups: dict[str, dict[str, str]],
) -> None:
    """Keep the client token identical to Nautobot's superuser API token."""
    existing_tokens: list[str] = []
    if token := read_group("nautobot").get("token"):
        existing_tokens.append(token)
    if "nautobot_app" in desired_groups and (
        token := read_group("nautobot_app").get("superuserApiToken")
    ):
        existing_tokens.append(token)

    if len(set(existing_tokens)) > 1:
        raise OpenBaoError(
            "Vault Nautobot client token and superuser API token differ; "
            "rotate them to the same value before running the installer"
        )
    token = existing_tokens[0] if existing_tokens else desired_groups["nautobot"]["token"]
    desired_groups["nautobot"]["token"] = token
    if "nautobot_app" in desired_groups:
        desired_groups["nautobot_app"]["superuserApiToken"] = token
