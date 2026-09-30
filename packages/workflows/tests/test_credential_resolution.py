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
"""Tests for mapping-only credential resolution helpers."""

import logging

import pytest

from nv_config_manager_workflows.secrets import (
    get_credential,
    get_rotation_passwords,
    get_site_slug,
    select_credential_source,
)


def test_site_slug_preserves_legacy_normalization() -> None:
    assert get_site_slug("My Data Center") == "my-data-center"
    assert get_site_slug(" Site_A ") == "-site_a-"


def test_site_specific_section_overrides_global_section() -> None:
    main = {"device": {"password": "global-secret"}}
    secrets = {"site.site-a": {"password": "site-secret"}}

    selected, section = select_credential_source(main, secrets, "device", "Site A")

    assert selected is secrets
    assert section == "site.site-a"
    assert get_credential(main, secrets, "device", "password", "Site A") == "site-secret"


def test_missing_site_section_falls_back_to_global_section() -> None:
    main = {"device": {"password": "global-secret"}}
    secrets = {"site.other": {"password": "other-secret"}}

    selected, section = select_credential_source(main, secrets, "device", "Site A")

    assert selected is main
    assert section == "device"
    assert get_credential(main, secrets, "device", "password", "Site A") == "global-secret"


def test_none_or_empty_secrets_mapping_uses_global_section() -> None:
    main = {"device": {"password": "global-secret"}}

    assert get_credential(main, None, "device", "password", "Site A") == "global-secret"
    assert get_credential(main, {}, "device", "password", "Site A") == "global-secret"


def test_missing_site_key_falls_back_to_global_key() -> None:
    main = {"jira": {"base_url": "https://jira.example.com", "api_token": "global-token"}}
    secrets = {"site.site-a": {"api_token": ""}}

    assert get_credential(main, secrets, "jira", "base_url", "Site A") == "https://jira.example.com"
    assert get_credential(main, secrets, "jira", "api_token", "Site A") == "global-token"


def test_missing_global_key_and_section_return_default() -> None:
    assert get_credential({"jira": {}}, None, "jira", "api_token", default="fallback") == (
        "fallback"
    )
    assert get_credential({}, None, "jira", "api_token", default="fallback") == "fallback"


def test_rotation_passwords_are_ordered_and_limited() -> None:
    config = {
        "device": {
            "api_user_key_r2": "second",
            "api_user_key_r10": "newest",
            "api_user_key_r1": "oldest",
            "api_user_key_invalid": "ignored-secret",
            "unrelated": "ignored",
        }
    }

    assert get_rotation_passwords(config, "device") == ["newest", "second"]
    assert get_rotation_passwords(config, "device", max_passwords=3) == [
        "newest",
        "second",
        "oldest",
    ]
    assert get_rotation_passwords(config, "device", max_passwords=0) == []
    assert get_rotation_passwords(config, "missing") == []


def test_rotation_passwords_support_custom_prefix() -> None:
    config = {
        "ufm": {
            "ufm_api_token_r1": "old",
            "ufm_api_token_r3": "new",
            "api_user_key_r9": "wrong-prefix",
        }
    }

    assert get_rotation_passwords(config, "ufm", key_prefix="ufm_api_token_r") == [
        "new",
        "old",
    ]


def test_credential_helpers_do_not_log_secret_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "sentinel-secret-never-log"

    main = {"device": {"password": secret, "api_user_key_r1": secret}}
    with caplog.at_level(logging.DEBUG, logger="nv_config_manager_workflows.secrets"):
        assert get_credential(main, None, "device", "password") == secret
        assert get_rotation_passwords(main, "device") == [secret]

    assert secret not in caplog.text
    assert "Found rotation key: api_user_key_r1 (revision 1) in [device]" in caplog.text


def test_rotation_passwords_log_invalid_keys_without_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "invalid-key-secret-never-log"

    with caplog.at_level(logging.DEBUG, logger="nv_config_manager_workflows.secrets"):
        assert (
            get_rotation_passwords(
                {"device": {"api_user_key_rinvalid": secret}},
                "device",
            )
            == []
        )

    assert "Skipping invalid rotation key: api_user_key_rinvalid" in caplog.text
    assert secret not in caplog.text
