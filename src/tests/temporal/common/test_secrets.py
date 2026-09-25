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
"""Tests for site-specific secrets INI loading."""

from pathlib import Path

import pytest

from nv_config_manager.temporal.common.secrets import (
    clear_secrets_cache,
    load_secrets_config,
)


def test_secrets_config_reuses_cached_parser_when_file_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    secrets_file = tmp_path / "config-secrets.ini"
    secrets_file.write_text("[site.test]\napi_user_key_r1 = secret\n")
    monkeypatch.setenv("NV_CONFIG_MANAGER_CONFIG_SECRET_PATH", str(secrets_file))
    clear_secrets_cache()

    first, first_found = load_secrets_config()
    second, second_found = load_secrets_config()

    assert first_found is second_found is True
    assert second is first


def test_secrets_config_reloads_after_file_update(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
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
