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
"""Tests for config store configuration."""

from nv_config_manager.config_store.config import Settings


def test_cors_origins_loaded_from_default():
    """Test that CORS origins are loaded from default config."""
    # Default conftest INI has config_store.api.cors_origins set
    settings = Settings()
    assert settings.cors_origins == ["https://config-manager.example.com"]


def test_cors_origins_strips_whitespace_and_filters_empty(custom_ini):
    """Test that CORS origins are stripped and empty strings filtered."""
    custom_ini(
        """
        [config_store.api]
        cors_origins =  https://example.com  ,,  https://test.com  ,
        """
    )

    settings = Settings()
    assert settings.cors_origins == ["https://example.com", "https://test.com"]


def test_dcim_settings_override_legacy_nautobot_settings(custom_ini):
    """Config Store follows canonical generic DCIM connection settings."""
    custom_ini(
        """
        [dcim]
        provider = nautobot-2x
        server = https://dcim.example.com
        token = dcim-token
        cache_refresh_interval = 120
        cache_ttl = 240
        """
    )

    settings = Settings()

    assert settings.nautobot_url == "https://dcim.example.com"
    assert settings.nautobot_token == "dcim-token"
    assert settings.nautobot_cache_refresh_interval == 120
    assert settings.nautobot_cache_ttl == 240
