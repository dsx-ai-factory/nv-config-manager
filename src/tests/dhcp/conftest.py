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
"""DHCP test configuration - INI mocking handled by top-level conftest.py."""

from pathlib import Path

import pytest

from nv_config_manager.dhcp.config_hash import EXPECTED_CONFIG_HASH_FILE_ENV


@pytest.fixture(autouse=True)
def isolate_expected_config_hash_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep each DHCP test's readiness state in its own temporary directory."""
    monkeypatch.setenv(EXPECTED_CONFIG_HASH_FILE_ENV, str(tmp_path / "expected-config-hash-v4"))
