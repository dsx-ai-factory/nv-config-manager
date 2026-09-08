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

import json

from nv_config_manager.temporal.client.device import MockNetworkConnection

_TEST_HOST = "192.0.2.1"


def test_mock_run_diagnostic_command_returns_valid_json():
    """run_diagnostic_command returns a valid JSON string with the expected keys."""
    conn = MockNetworkConnection(_TEST_HOST)
    raw = conn.run_diagnostic_command("show_version")
    parsed = json.loads(raw)
    assert "mock" in parsed
    assert "command" in parsed


def test_mock_run_diagnostic_command_includes_command_name():
    """The 'command' field in the returned JSON matches the input name."""
    conn = MockNetworkConnection(_TEST_HOST)
    raw = conn.run_diagnostic_command("show_bgp_summary")
    parsed = json.loads(raw)
    assert parsed["command"] == "show_bgp_summary"


def test_mock_get_tech_support_bundle_returns_bytes():
    """get_tech_support_bundle returns (bytes, log_str)."""
    conn = MockNetworkConnection(_TEST_HOST)
    content, log = conn.get_tech_support_bundle()
    assert isinstance(content, bytes)
    assert content == b"[mock tech-support bundle]"
    assert isinstance(log, str)
