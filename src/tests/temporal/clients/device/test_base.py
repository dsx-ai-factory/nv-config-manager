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

from configparser import ConfigParser
from unittest.mock import patch

from nv_config_manager.temporal.client.device import NetworkConnection


class TestNetworkConnectionPasswordRotation:
    """Tests for NetworkConnection password initialization behavior."""

    def test_uses_rotation_passwords_sorted_by_revision(self):
        """Test that rotation passwords are used in order of revision (newest first)."""
        config = ConfigParser()
        config.add_section("device")
        config.set("device", "username", "admin")
        config.set("device", "api_user_key_r1", "old_pw")
        config.set("device", "api_user_key_r2", "new_pw")
        conn = NetworkConnection("host", 22, config=config)

        assert conn._passwords_to_try == ["new_pw", "old_pw"]

    def test_falls_back_to_password_when_no_rotation_keys(self):
        """Test fallback to password field when no rotation keys exist."""
        config = ConfigParser()
        config.add_section("device")
        config.set("device", "username", "admin")
        config.set("device", "password", "fallback_pw")
        conn = NetworkConnection("host", 22, config=config)

        assert conn._passwords_to_try == ["fallback_pw"]

    def test_uses_explicit_password_only(self):
        """Test that explicit password is used exclusively, ignoring config passwords."""
        config = ConfigParser()
        config.add_section("device")
        config.set("device", "username", "admin")
        config.set("device", "api_user_key_r1", "config_pw")
        conn = NetworkConnection("host", 22, password="explicit_pw", config=config)

        assert conn._passwords_to_try == ["explicit_pw"]

    def test_no_passwords_available(self):
        """Test empty password list when no passwords configured."""
        config = ConfigParser()
        config.add_section("device")
        config.set("device", "username", "admin")
        conn = NetworkConnection("host", 22, config=config)

        assert conn._passwords_to_try == []

    def test_uses_secrets_config_for_site_passwords(self, tmp_path):
        """Test that secrets config is used for site-specific passwords when available."""
        # Setup main config (will be used for username only)
        main_config = ConfigParser()
        main_config.add_section("device")
        main_config.set("device", "username", "admin")
        main_config.set("device", "api_user_key_r1", "main_pw")
        # Create secrets config file
        secrets_file = tmp_path / "config-secrets.ini"
        secrets_file.write_text("[site.site-a]\napi_user_key_r1 = secrets_pw\n")

        with patch.dict("os.environ", {"NV_CONFIG_MANAGER_CONFIG_SECRET_PATH": str(secrets_file)}):
            conn = NetworkConnection("host", 22, site="Site A", config=main_config)

            assert conn._passwords_to_try == ["secrets_pw"]

    def test_falls_back_to_main_config_when_no_secrets_file(self):
        """Test fallback to main config global section when secrets file doesn't exist."""
        # Setup main config with global device section only
        main_config = ConfigParser()
        main_config.add_section("device")
        main_config.set("device", "username", "admin")
        main_config.set("device", "api_user_key_r1", "main_global_pw")
        # Point to non-existent file
        with patch.dict(
            "os.environ",
            {"NV_CONFIG_MANAGER_CONFIG_SECRET_PATH": "/nonexistent/config-secrets.ini"},
        ):
            # Site is provided but secrets file doesn't exist, falls back to global
            conn = NetworkConnection("host", 22, site="Site A", config=main_config)

            assert conn._passwords_to_try == ["main_global_pw"]

    def test_falls_back_to_global_when_site_not_in_secrets(self):
        """Test fallback to global device section when secrets env var is not set."""
        # Setup main config with global section only
        main_config = ConfigParser()
        main_config.add_section("device")
        main_config.set("device", "username", "admin")
        main_config.set("device", "api_user_key_r1", "main_global_pw")
        # Don't set NV_CONFIG_MANAGER_CONFIG_SECRET_PATH - secrets config won't be loaded
        # This tests the fallback to main config when no secrets file is configured
        conn = NetworkConnection("host", 22, site="Site B", config=main_config)

        # No secrets config, falls back to main config's global device section
        assert conn._passwords_to_try == ["main_global_pw"]
