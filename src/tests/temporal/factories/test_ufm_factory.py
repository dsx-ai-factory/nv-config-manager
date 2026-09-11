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
"""Tests for service-owned UFM credential and client construction."""

from collections.abc import Iterator
from configparser import ConfigParser
from pathlib import Path
from ssl import PROTOCOL_TLS_CLIENT, SSLContext
from unittest.mock import Mock

import pytest

from nv_config_manager.common import config_loader as config_module
from nv_config_manager.temporal.common.secrets import clear_secrets_cache
from nv_config_manager.temporal.factories.ufm import (
    create_ufm_client,
    ufm_client_settings,
)


@pytest.fixture(autouse=True)
def reset_secrets_cache() -> Iterator[None]:
    """Keep the service secrets-file cache isolated between tests."""
    clear_secrets_cache()
    yield
    clear_secrets_cache()


@pytest.fixture
def main_config() -> ConfigParser:
    """Return global UFM fallback credentials."""
    config = ConfigParser()
    config.read_dict(
        {
            "ufm": {
                "ufm_api_user": "global-user",
                "ufm_api_token_r1": "global-old",
                "ufm_api_token_r5": "global-new",
            }
        }
    )
    return config


@pytest.fixture
def multi_site_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Install a secrets file containing two independently rotated sites."""
    secrets_path = tmp_path / "config-secrets.ini"
    secrets_path.write_text(
        "[site.alpha-site]\n"
        "ufm_api_user = alpha-user\n"
        "ufm_api_token_r1 = alpha-old\n"
        "ufm_api_token_r3 = alpha-middle\n"
        "ufm_api_token_r8 = alpha-new\n"
        "\n"
        "[site.beta-site]\n"
        "ufm_api_user = beta-user\n"
        "ufm_api_token_r2 = beta-old\n"
        "ufm_api_token_r4 = beta-new\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("NV_CONFIG_MANAGER_CONFIG_SECRET_PATH", str(secrets_path))
    return secrets_path


@pytest.mark.parametrize(
    ("site", "expected_username", "expected_passwords"),
    [
        ("Alpha Site", "alpha-user", ["alpha-new", "alpha-middle"]),
        ("Beta Site", "beta-user", ["beta-new", "beta-old"]),
        ("Missing Site", "global-user", ["global-new", "global-old"]),
        (None, "global-user", ["global-new", "global-old"]),
    ],
)
def test_settings_select_every_site_and_global_fallback(
    main_config: ConfigParser,
    multi_site_secrets: Path,
    site: str | None,
    expected_username: str,
    expected_passwords: list[str],
) -> None:
    assert multi_site_secrets.is_file()

    assert ufm_client_settings(main_config, site=site) == {
        "username": expected_username,
        "passwords": expected_passwords,
    }


def test_settings_preserve_password_limit_and_revision_order(
    main_config: ConfigParser,
    multi_site_secrets: Path,
) -> None:
    assert multi_site_secrets.is_file()

    assert ufm_client_settings(main_config, site="Alpha Site", max_passwords=3) == {
        "username": "alpha-user",
        "passwords": ["alpha-new", "alpha-middle", "alpha-old"],
    }


def test_missing_secrets_file_falls_back_to_global_credentials(
    main_config: ConfigParser,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "NV_CONFIG_MANAGER_CONFIG_SECRET_PATH",
        str(tmp_path / "missing.ini"),
    )

    assert ufm_client_settings(main_config, site="Alpha Site") == {
        "username": "global-user",
        "passwords": ["global-new", "global-old"],
    }


def test_service_factory_passes_explicit_settings_tls_and_timeout(
    main_config: ConfigParser,
    multi_site_secrets: Path,
) -> None:
    assert multi_site_secrets.is_file()
    ssl_context = SSLContext(PROTOCOL_TLS_CLIENT)

    client = create_ufm_client(
        "alpha-ufm.example.com",
        main_config,
        site="Alpha Site",
        max_passwords=3,
        ssl=ssl_context,
        timeout_seconds=47,
    )

    assert client._base_url == "https://alpha-ufm.example.com/ufmRest"
    assert client._host == "alpha-ufm.example.com"
    assert client._username == "alpha-user"
    assert client._passwords == ["alpha-new", "alpha-middle", "alpha-old"]
    assert client._ssl is ssl_context
    assert client._timeout_seconds == 47


def test_injected_configuration_does_not_load_global_config(
    main_config: ConfigParser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load_config = Mock(side_effect=AssertionError("load_config should not be called"))
    monkeypatch.setattr(config_module, "load_config", load_config)

    create_ufm_client(
        "ufm.example.com",
        main_config,
        max_passwords=1,
    )

    load_config.assert_not_called()
