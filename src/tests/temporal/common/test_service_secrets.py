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
"""Tests for service-owned site-specific secrets loading and compatibility."""

import logging
import os
from configparser import ConfigParser
from unittest.mock import patch

from nv_config_manager.temporal.common import secrets as secrets_module
from nv_config_manager.temporal.common.secrets import (
    clear_secrets_cache,
    get_credential,
    get_rotation_passwords,
    get_site_slug,
    load_secrets_config,
    resolve_credential_source,
    resolve_credentials,
)


def test_secrets_config_reloads_after_file_update(monkeypatch, tmp_path):
    secrets_file = tmp_path / "config-secrets.ini"
    secrets_file.write_text("[site.test]\napi_user_key_r1 = old-secret\n")
    monkeypatch.setenv("NV_CONFIG_MANAGER_CONFIG_SECRET_PATH", str(secrets_file))
    clear_secrets_cache()

    first, first_found = load_secrets_config()
    assert first_found is True
    assert first["site.test"]["api_user_key_r1"] == "old-secret"

    secrets_file.write_text("[site.test]\napi_user_key_r1 = new-secret\n")
    second, second_found = load_secrets_config()

    assert second_found is True
    assert second is not first
    assert second["site.test"]["api_user_key_r1"] == "new-secret"


def test_secrets_config_reloads_after_kubernetes_style_symlink_swap(monkeypatch, tmp_path):
    version_one = tmp_path / "..2026_01"
    version_two = tmp_path / "..2026_02"
    version_one.mkdir()
    version_two.mkdir()
    first_file = version_one / "config-secrets.ini"
    second_file = version_two / "config-secrets.ini"
    first_file.write_text("[site.test]\npassword = one\n")
    second_file.write_text("[site.test]\npassword = two\n")

    timestamp_ns = 1_700_000_000_000_000_000
    os.utime(first_file, ns=(timestamp_ns, timestamp_ns))
    os.utime(second_file, ns=(timestamp_ns, timestamp_ns))

    data_link = tmp_path / "..data"
    data_link.symlink_to(version_one.name, target_is_directory=True)
    secrets_file = tmp_path / "config-secrets.ini"
    secrets_file.symlink_to("..data/config-secrets.ini")
    monkeypatch.setenv("NV_CONFIG_MANAGER_CONFIG_SECRET_PATH", str(secrets_file))
    clear_secrets_cache()
    first, _ = load_secrets_config()

    replacement_link = tmp_path / "..data-next"
    replacement_link.symlink_to(version_two.name, target_is_directory=True)
    replacement_link.replace(data_link)
    second, _ = load_secrets_config()

    assert second is not first
    assert second["site.test"]["password"] == "two"


def test_compatibility_functions_delegate_with_existing_signatures(monkeypatch, tmp_path):
    secrets_file = tmp_path / "config-secrets.ini"
    secrets_file.write_text(
        "[site.alpha-site]\n"
        "username = site-user\n"
        "api_user_key_r2 = newer\n"
        "api_user_key_r1 = older\n"
    )
    monkeypatch.setenv("NV_CONFIG_MANAGER_CONFIG_SECRET_PATH", str(secrets_file))
    clear_secrets_cache()
    main = ConfigParser()
    main.read_dict({"device": {"username": "global-user", "password": "fallback"}})

    selected, section = resolve_credential_source(main, "device", "Alpha Site")

    assert selected[section]["username"] == "site-user"
    assert section == "site.alpha-site"
    assert get_site_slug("Alpha Site") == "alpha-site"
    assert resolve_credentials(main, "device", "Alpha Site")["username"] == "site-user"
    assert get_credential(main, "device", "username", "Alpha Site") == "site-user"
    assert get_credential(main, "device", "password", "Alpha Site") == "fallback"
    assert get_rotation_passwords(selected, section) == ["newer", "older"]


def test_missing_secrets_file_uses_global_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "NV_CONFIG_MANAGER_CONFIG_SECRET_PATH",
        str(tmp_path / "missing.ini"),
    )
    clear_secrets_cache()
    main = ConfigParser()
    main.read_dict({"device": {"password": "fallback"}})

    selected, section = resolve_credential_source(main, "device", "Alpha Site")

    assert selected is main
    assert section == "device"
    assert get_credential(main, "device", "password", "Alpha Site") == "fallback"


def test_each_lookup_selects_credential_source_once(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "NV_CONFIG_MANAGER_CONFIG_SECRET_PATH",
        str(tmp_path / "missing.ini"),
    )
    clear_secrets_cache()
    main = ConfigParser()
    main.read_dict({"device": {"password": "fallback"}})
    original_select = secrets_module.select_credential_source
    call_count = 0

    def track_selection(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return original_select(*args, **kwargs)

    monkeypatch.setattr(secrets_module, "select_credential_source", track_selection)

    resolve_credential_source(main, "device", "Alpha Site")
    resolve_credentials(main, "device", "Alpha Site")
    get_credential(main, "device", "password", "Alpha Site")

    assert call_count == 3


def test_compatibility_shim_does_not_log_secret_values(monkeypatch, tmp_path, caplog):
    secrets_file = tmp_path / "config-secrets.ini"
    secrets_file.write_text("[site.alpha-site]\npassword = do-not-log-me\n")
    monkeypatch.setenv("NV_CONFIG_MANAGER_CONFIG_SECRET_PATH", str(secrets_file))
    clear_secrets_cache()
    main = ConfigParser()
    main.read_dict({"device": {"password": "also-secret"}})

    with caplog.at_level(logging.DEBUG):
        assert get_credential(main, "device", "password", "Alpha Site") == "do-not-log-me"

    assert "do-not-log-me" not in caplog.text
    assert "also-secret" not in caplog.text


def test_compatibility_shim_logs_selected_source(monkeypatch, tmp_path):
    secrets_file = tmp_path / "config-secrets.ini"
    secrets_file.write_text("[site.alpha-site]\npassword = secret\n")
    monkeypatch.setenv("NV_CONFIG_MANAGER_CONFIG_SECRET_PATH", str(secrets_file))
    clear_secrets_cache()
    main = ConfigParser()
    main.read_dict({"device": {"password": "fallback"}})

    with patch.object(secrets_module.logger, "debug") as debug:
        get_credential(main, "device", "password", "Alpha Site")

    debug.assert_any_call(
        "Using site-specific secrets config section: [%s]",
        "site.alpha-site",
    )
