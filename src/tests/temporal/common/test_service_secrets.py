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
"""Compatibility tests for service-side secrets loading and delegation."""

from configparser import ConfigParser
from pathlib import Path

import pytest

from nv_config_manager.temporal.common.secrets import (
    clear_secrets_cache,
    get_credential,
    get_rotation_passwords,
    get_site_slug,
    resolve_config_section,
)


def _main_config() -> ConfigParser:
    config = ConfigParser(interpolation=None)
    config.read_dict(
        {
            "device": {
                "password": "global-password",
                "api_user_key_r1": "global-old",
                "api_user_key_r2": "global-new",
            }
        }
    )
    return config


def test_compatibility_shim_selects_site_section(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    secrets_path = tmp_path / "config-secrets.ini"
    secrets_path.write_text("[site.site-a]\npassword = site-password\napi_user_key_r3 = site-new\n")
    monkeypatch.setenv("NV_CONFIG_MANAGER_CONFIG_SECRET_PATH", str(secrets_path))
    clear_secrets_cache()
    main = _main_config()

    selected, section = resolve_config_section(main, "device", "Site A")

    assert selected is not main
    assert section == "site.site-a"
    assert get_credential(main, "device", "password", "Site A") == "site-password"
    assert get_rotation_passwords(selected, section) == ["site-new"]


def test_compatibility_shim_falls_back_to_global_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    secrets_path = tmp_path / "config-secrets.ini"
    secrets_path.write_text("[site.site-a]\napi_user_key_r3 = site-new\n")
    monkeypatch.setenv("NV_CONFIG_MANAGER_CONFIG_SECRET_PATH", str(secrets_path))
    clear_secrets_cache()

    assert get_credential(_main_config(), "device", "password", "Site A") == ("global-password")


def test_compatibility_shim_handles_missing_secrets_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(
        "NV_CONFIG_MANAGER_CONFIG_SECRET_PATH",
        str(tmp_path / "missing.ini"),
    )
    clear_secrets_cache()
    main = _main_config()

    selected, section = resolve_config_section(main, "device", "Site A")

    assert selected is main
    assert section == "device"
    assert get_credential(main, "device", "password", "Site A") == "global-password"
    assert get_rotation_passwords(selected, section) == ["global-new", "global-old"]


def test_compatibility_shim_preserves_site_slug() -> None:
    assert get_site_slug("My Data Center") == "my-data-center"
