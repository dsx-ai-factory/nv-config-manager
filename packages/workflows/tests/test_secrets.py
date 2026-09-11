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
"""Tests for pure credential resolution."""

from __future__ import annotations

import builtins

import pytest

from nv_config_manager_workflows import secrets as secrets_module
from nv_config_manager_workflows.secrets import (
    get_credential,
    get_rotation_passwords,
    get_site_slug,
    select_credential_source,
)

MAIN_CONFIG = {
    "device": {
        "username": "global-user",
        "password": "global-password",
    }
}
SECRETS_CONFIG = {
    "site.alpha-site": {
        "username": "site-user",
        "password": "site-password",
    }
}


def test_site_slug_matches_existing_section_format() -> None:
    assert get_site_slug("Alpha Site") == "alpha-site"


def test_site_specific_section_overrides_global_section() -> None:
    selected, section = select_credential_source(
        MAIN_CONFIG,
        SECRETS_CONFIG,
        "device",
        "Alpha Site",
    )

    assert selected is SECRETS_CONFIG
    assert section == "site.alpha-site"


@pytest.mark.parametrize("secrets_config", [None, {}])
def test_missing_secrets_mapping_falls_back_to_global_section(secrets_config) -> None:
    selected, section = select_credential_source(
        MAIN_CONFIG,
        secrets_config,
        "device",
        "Alpha Site",
    )

    assert selected is MAIN_CONFIG
    assert section == "device"


def test_missing_site_key_falls_back_to_global_value() -> None:
    site_without_password = {"site.alpha-site": {"username": "site-user"}}

    assert (
        get_credential(
            MAIN_CONFIG,
            site_without_password,
            "device",
            "password",
            "Alpha Site",
        )
        == "global-password"
    )
    assert (
        get_credential(
            MAIN_CONFIG,
            site_without_password,
            "device",
            "missing",
            "Alpha Site",
            "default-value",
        )
        == "default-value"
    )


def test_rotation_passwords_are_newest_first_limited_and_ignore_invalid_keys() -> None:
    config = {
        "device": {
            "api_user_key_r1": "oldest",
            "api_user_key_r10": "newest",
            "api_user_key_r3": "middle",
            "api_user_key_rx": "invalid",
            "other": "ignored",
        }
    }

    assert get_rotation_passwords(config, "device") == ["newest", "middle"]
    assert get_rotation_passwords(config, "device", max_passwords=3) == [
        "newest",
        "middle",
        "oldest",
    ]
    assert get_rotation_passwords(config, "missing") == []


def test_resolution_has_no_file_configuration_or_secret_logging(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fail_open(*args: object, **kwargs: object) -> None:
        raise AssertionError("credential resolution must not read files")

    monkeypatch.setattr(builtins, "open", fail_open)

    assert not hasattr(secrets_module, "ConfigParser")
    assert not hasattr(secrets_module, "load_config")
    assert (
        get_credential(
            MAIN_CONFIG,
            SECRETS_CONFIG,
            "device",
            "password",
            "Alpha Site",
        )
        == "site-password"
    )
    assert "site-password" not in caplog.text
    assert "global-password" not in caplog.text
