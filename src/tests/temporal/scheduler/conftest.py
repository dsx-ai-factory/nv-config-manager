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
"""Fixtures shared by workflow scheduler host tests."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from nv_config_manager import dcim
from nv_config_manager.common.config import clear_config_cache, load_config
from nv_config_manager.dcim import registry as dcim_registry
from nv_config_manager_workflows.schedulers import runtime as scheduler_runtime
from tests.temporal.scheduler.helpers import SECRET_SENTINEL


@pytest.fixture
def secret_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Load a service INI whose credentials and endpoints are sentinel secrets."""
    config_path = tmp_path / "nv-config-manager.ini"
    config_path.write_text(
        f"""
[dcim]
provider = fixture-dcim

[dcim.options]
token = {SECRET_SENTINEL}

[nautobot]
url = https://{SECRET_SENTINEL}.example.invalid
token = {SECRET_SENTINEL}

[temporal]
api_url = https://{SECRET_SENTINEL}.example.invalid
tls_client_key = {SECRET_SENTINEL}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("NV_CONFIG_MANAGER_INI", str(config_path))
    clear_config_cache()
    try:
        assert load_config().get("nautobot", "token") == SECRET_SENTINEL
        yield
    finally:
        clear_config_cache()


@pytest.fixture
def non_nautobot_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Load a service INI that selects a DCIM provider other than Nautobot 2.x."""
    config_path = tmp_path / "nv-config-manager.ini"
    config_path.write_text(
        """
[dcim]
provider = fixture-dcim
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("NV_CONFIG_MANAGER_INI", str(config_path))
    clear_config_cache()
    try:
        assert load_config().get("dcim", "provider") == "fixture-dcim"
        yield
    finally:
        clear_config_cache()


@pytest.fixture
def dcim_provider_must_not_be_consulted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail if scheduler selection or host startup reads the configured DCIM provider."""

    def fail(*args: object, **kwargs: object) -> str:
        pytest.fail("the scheduler host must not consult the DCIM provider")

    monkeypatch.setattr(dcim_registry, "configured_dcim_provider_name", fail)
    monkeypatch.setattr(dcim, "configured_dcim_provider_name", fail)


@pytest.fixture
def unset_scheduler_runtimes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start unconfigured and restore process-global scheduler runtimes afterwards."""
    monkeypatch.setattr(scheduler_runtime, "_scheduler_runtime", scheduler_runtime._UNSET)
    monkeypatch.setattr(
        scheduler_runtime,
        "_builtin_scheduler_runtime",
        scheduler_runtime._UNSET,
    )
