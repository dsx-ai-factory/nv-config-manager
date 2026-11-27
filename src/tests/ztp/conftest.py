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
"""ZTP test configuration - INI mocking handled by top-level conftest.py."""

import json
import os

import pytest

from nv_config_manager.ztp.api.device_reads import reset_device_reads

# Get the directory containing this conftest.py
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture(autouse=True)
def _reset_device_reads():
    """Keep one test's cached device from answering the next test."""
    reset_device_reads()
    yield
    reset_device_reads()


@pytest.fixture
def mock_device_data():
    with open(os.path.join(_THIS_DIR, "resources/device_data.json")) as f:
        return json.load(f)


@pytest.fixture
def mock_not_found_data():
    with open(os.path.join(_THIS_DIR, "resources/null.json")) as f:
        return json.load(f)


@pytest.fixture
def mock_no_render_data():
    with open(os.path.join(_THIS_DIR, "resources/device_data_no_render.json")) as f:
        return json.load(f)
