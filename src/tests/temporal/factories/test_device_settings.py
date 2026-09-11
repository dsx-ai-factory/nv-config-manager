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
"""Credential precedence at the device service/settings boundary."""

import logging
from configparser import ConfigParser
from pathlib import Path
from unittest.mock import patch

import pytest

from nv_config_manager.temporal.client.device import NetworkConnection
from nv_config_manager.temporal.factories.device import device_connection_settings


@pytest.mark.parametrize(
    ("global_passwords", "site_values", "username", "password"),
    [
        (
            {"api_user_key_r1": "old", "api_user_key_r3": "new", "api_user_key_r2": "middle"},
            None,
            None,
            None,
        ),
        (
            {"password": "global-password-sentinel"},
            {"api_user_key_r2": "site-password-sentinel"},
            None,
            None,
        ),
        ({"password": "global-password-sentinel"}, {"username": "ignored-site-user"}, None, None),
        (
            {"api_user_key_r1": "global-rotation", "password": "global-password-sentinel"},
            {"password": "site-password-sentinel"},
            None,
            None,
        ),
        ({"password": "global-password-sentinel"}, None, "", ""),
        ({"password": "global-password-sentinel"}, None, "explicit-user", "explicit-password"),
        ({}, None, None, None),
    ],
)
def test_settings_match_legacy_credential_selection(
    global_passwords: dict[str, str],
    site_values: dict[str, str] | None,
    username: str | None,
    password: str | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = ConfigParser(interpolation=None)
    config.read_dict({"device": {"username": "global-user", **global_passwords}})
    secrets_path = tmp_path / "secrets.ini"
    if site_values is not None:
        secrets = ConfigParser(interpolation=None)
        secrets.read_dict({"site.site-a": site_values})
        with secrets_path.open("w") as stream:
            secrets.write(stream)
    monkeypatch.setenv("NV_CONFIG_MANAGER_CONFIG_SECRET_PATH", str(secrets_path))

    with patch("nv_config_manager.temporal.client.device.base.load_config", return_value=config):
        legacy = NetworkConnection("host", 22, username, password, site="Site A")

    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        settings = device_connection_settings(
            config, site="Site A", username=username, password=password
        )

    assert settings["username"] == legacy._username
    assert settings["passwords"] == legacy._passwords_to_try
    assert settings["mock"] is False
    for value in [settings["username"], *settings["passwords"]]:
        assert value not in caplog.text


def test_explicit_password_skips_secret_lookup() -> None:
    config = ConfigParser()
    config.read_dict({"device": {"mock": "yes"}})
    with patch(
        "nv_config_manager.temporal.factories.device.resolve_credential_source",
        side_effect=AssertionError("Explicit credentials must skip secret lookup"),
    ):
        assert device_connection_settings(config, username="user", password="secret") == {
            "username": "user",
            "passwords": ["secret"],
            "mock": True,
        }
