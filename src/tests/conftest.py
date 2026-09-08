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
"""Top-level pytest configuration and shared fixtures."""

import configparser
from collections.abc import Generator
from typing import Any
from unittest.mock import Mock, patch

import pytest
from aiohttp import ClientResponse

from nv_config_manager.common import auth as auth_mod
from nv_config_manager.common.config import clear_config_cache

_CLIENT_RESPONSE_INIT = ClientResponse.__init__


def _client_response_init_with_stream_writer(
    self: ClientResponse, *args: Any, **kwargs: Any
) -> None:
    """Bridge aioresponses to the aiohttp 3.14 ClientResponse signature."""
    kwargs.setdefault("stream_writer", Mock(output_size=0))
    _CLIENT_RESPONSE_INIT(self, *args, **kwargs)


@pytest.fixture(scope="session", autouse=True)
def aiohttp_mock_response_compatibility() -> Generator[None]:
    """Supply the argument omitted by the latest aioresponses release."""
    with patch.object(ClientResponse, "__init__", _client_response_init_with_stream_writer):
        yield


# Default INI content that covers all common sections
# All hostnames are generic - no vendor-specific references
DEFAULT_INI = """
[aggregate]
is_aggregate_environment = false

[auth]
required = true
accept_request_headers = true

[dcim]
provider = nautobot-2x
server = https://nautobot.example.com/
token = DUMMY
verify = true

[nats]
server = nats://ruser:T0pS3cr3t@nats.example.local:4222
creds_path = /etc/vault/nats-creds
queue = nv-config-manager
auth_method = none
local = true
config_manager_stream = nv-config-manager
config_manager_api_prefix = $JS.API
config_manager_subjects = nv-config-manager.nautobotchange,nv-config-manager.devicechange,nv-config-manager.workflow.result
render_change_stream = nv-config-manager
render_change_subject = nv-config-manager.nautobotchange
device_change_stream = nv-config-manager
device_change_subject = nv-config-manager.devicechange
archive_stream = nv-config-manager
archive_subject = nv-config-manager.workflow.result
nautobot_stream = nautobot
nautobot_api_prefix = $JS.API
nautobot_subjects = nautobot
nautobot_subject = nautobot

[redis]
host = localhost
port = 6379
db = 0
lock_db = 0
ssl = false

[config_store]
database_host = localhost
database_port = 5432
database = nv_config_manager_config_store
database_user = config_store_user
database_password = DUMMY

[config_store.api]
cors_origins = https://config-manager.example.com

[config_store.client]
api_service = http://config-store-api.example.local:8080
use_internal_endpoint = true
ui_url = https://config-manager.example.com

[render]
api_service = http://render-api.example.local:9000
use_internal_endpoint = true

[ztp]
api_service = http://ztp-api.example.local:9000
use_internal_endpoint = true
user_domain = ztp.example.com
http_stream_chunk_bytes = 67108864
http_max_concurrent_downloads = 16
sftp_read_ahead_bytes = 16777216
sftp_max_concurrent_downloads = 32
sftp_metrics_port = 9100

[temporal]
grpc_service = temporal-frontend.example.local:7233
api_service = http://temporal-api.example.local:9000
api_url = https://temporal-api.example.com
temporal_ui_url = https://temporal-ui.example.com
ui_url = https://temporal-ui.example.com
use_internal_endpoint = true

[temporal.elasticsearch]
local = true
server = elasticsearch.example.local:9200
domain = nv-config-manager
user = elastic
password = elastic

[temporal.api]
cors_origins = https://config-manager.example.com

[dhcp.kea]
server = localhost
port = 8000

[dhcp.lease_db]
local = no
host = dhcp-db.example.local
database = kea_dhcp
user = kea_user
password = DUMMY

[device]
username = svc-nv-config-manager
password = DUMMY

[redfish]
lenovo_default_user = LENOVO_DEFAULT_USER
lenovo_default_password = LENOVO_DEFAULT_PASSWORD
lenovo_config_manager_password = LENOVO_CONFIG_MANAGER_PASSWORD
bluefield_default_user = BLUEFIELD_DEFAULT_USER
bluefield_default_password = BLUEFIELD_DEFAULT_PASSWORD
bluefield_config_manager_password = BLUEFIELD_CONFIG_MANAGER_PASSWORD

[mtls]
tls_client_cert_path = /etc/tls-client/tls.crt
tls_client_key_path = /etc/tls-client/tls.key

[slack]
bot_token = DUMMY
channel_name = nv-config-manager-test
"""

# Mutable container for current INI content - allows tests to override
_current_ini = {"content": DEFAULT_INI}


def _clear_auth_config_cache() -> None:
    """Clear derived auth state when the backing INI cache is reset."""
    auth_mod._auth_config = None
    auth_mod._auth_config_source = None
    auth_mod._auth_config_tracks_file = False


@pytest.fixture(autouse=True)
def mock_ini_config(mocker):
    """
    Auto-use fixture that mocks ConfigParser.read with comprehensive test config.

    This fixture runs automatically for all tests, providing a consistent
    INI configuration across the test suite.
    """
    # Reset to default INI for each test
    _current_ini["content"] = DEFAULT_INI

    # Clear any cached config from previous tests
    clear_config_cache()
    _clear_auth_config_cache()

    read_func = configparser.ConfigParser.read

    def mock_func(self, filenames, *args, **kwargs):
        self.read_string(_current_ini["content"])
        return read_func(self, filenames, *args, **kwargs)

    mocker.patch("configparser.ConfigParser.read", new=mock_func)

    yield

    # Clear cache after test to prevent leaking to next test
    clear_config_cache()
    _clear_auth_config_cache()


@pytest.fixture()
def custom_ini():
    """
    Fixture to override INI config with custom content for specific tests.

    Usage:
        def test_something(custom_ini):
            custom_ini('''
            [section]
            key = value
            ''')
            # Test code here

    Note: This also clears the load_config() cache to ensure the new config is used.
    """

    def _set_ini(ini_content: str):
        # Update the shared INI content
        _current_ini["content"] = ini_content
        # Clear the cached config so it will be reloaded with new settings
        clear_config_cache()

    return _set_ini
