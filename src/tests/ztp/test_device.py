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
"""Tests for device module."""

from unittest.mock import MagicMock, patch

from nv_config_manager.common.config import get_internal_auth_headers
from nv_config_manager.ztp.device import DeviceData


def test_config_store_client():
    """Test config_store_client uses internal service endpoint.

    Uses the global mock INI config from conftest.py which sets:
    - config_store.client.api_service = http://config-store-api.example.local:8080
    - config_store.client.use_internal_endpoint = true
    """
    device = DeviceData(
        id="80ce0a9a-d3c8-5b8e-b755-e9c16d92237b",
        name="test-device",
        addresses=["10.0.0.1"],
        platform_name="Cumulus Linux",
        version="5.7.0",
        config_store_instance="https://config-manager.example.com/",
    )

    with patch("nv_config_manager.ztp.device.ConfigStoreClient") as mock_client:
        mock_client_instance = MagicMock()
        mock_client.return_value = mock_client_instance

        client = device.config_store_client()

        # Verify the internal service endpoint is used without mTLS
        mock_client.assert_called_once()
        call_args = mock_client.call_args
        assert call_args[0][0] == "http://config-store-api.example.local:8080"
        assert call_args[0][1] == "intended"
        assert call_args[1]["verify"] is False  # No SSL verification for internal HTTP
        assert call_args[1]["client_certificate"] is None  # No mTLS for internal communication
        assert call_args[1]["headers"] is get_internal_auth_headers
        assert client == mock_client_instance


def test_config_store_client_external(custom_ini):
    """Test config_store_client uses external mTLS endpoint when internal is disabled."""
    custom_ini("""
[config_store.client]
api_service = http://config-store-api.example.local:8080
api_url = https://config-store.config-manager.example.com
use_internal_endpoint = false
ui_url = https://config-manager.example.com
verify = true

[mtls]
tls_client_cert_path = /etc/tls-client/tls.crt
tls_client_key_path = /etc/tls-client/tls.key
""")

    device = DeviceData(
        id="80ce0a9a-d3c8-5b8e-b755-e9c16d92237b",
        name="test-device",
        addresses=["10.0.0.1"],
        platform_name="Cumulus Linux",
        version="5.7.0",
        config_store_instance="https://config-manager.example.com/",
    )

    with patch("nv_config_manager.ztp.device.ConfigStoreClient") as mock_client:
        mock_client_instance = MagicMock()
        mock_client.return_value = mock_client_instance

        client = device.config_store_client()

        # Verify the external mTLS endpoint is used
        mock_client.assert_called_once()
        call_args = mock_client.call_args
        assert call_args[0][0] == "https://config-store.config-manager.example.com"
        assert call_args[0][1] == "intended"
        assert call_args[1]["verify"] is True
        assert client == mock_client_instance
