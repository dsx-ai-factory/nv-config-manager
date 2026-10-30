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
"""INI precedence and compatibility for shared application configuration."""

import pytest

from nv_config_manager.common.config.identity import default_user_domain
from nv_config_manager.common.telemetry import group_fastapi_status_codes
from nv_config_manager.render.producer import (
    template_update_concurrency,
    template_update_lock_timeout,
)
from nv_config_manager.temporal.scheduler.host import (
    SchedulerHostError,
    configured_scheduler_identities,
)
from nv_config_manager.temporal.worker.main import _registered_workflows
from nv_config_manager_workflows.registration.registry import WorkflowRegistry
from nv_config_manager_workflows.workflows import LOCAL_TEST_WORKFLOWS


def test_ini_settings_override_legacy_environment(custom_ini, monkeypatch):
    """All migrated consumers use INI even when old env overrides disagree."""
    custom_ini("""
[temporal]
user_domain = ini.example.com
[monitoring]
group_fastapi_status_codes = false
[render.template_updater]
concurrency = 3
lock_timeout = 27
[temporal.worker]
enable_local_test_workflows = true
[temporal.scheduler]
enabled_schedulers = fixture.cleanup, builtin.backup
""")
    for name, value in {
        "NV_CONFIG_MANAGER_DOMAIN": "legacy.example.com",
        "NV_CONFIG_MANAGER_GROUP_FASTAPI_STATUS_CODES": "true",
        "NV_CONFIG_MANAGER_TEMPLATE_UPDATE_CONCURRENCY": "99",
        "NV_CONFIG_MANAGER_TEMPLATE_UPDATE_LOCK_TIMEOUT": "99",
        "NVCM_ENABLE_LOCAL_TEST_WORKFLOWS": "false",
        "NVCM_ENABLED_SCHEDULERS": "legacy.scheduler",
    }.items():
        monkeypatch.setenv(name, value)

    assert default_user_domain() == "ini.example.com"
    assert group_fastapi_status_codes() is False
    assert template_update_concurrency() == 3
    assert template_update_lock_timeout() == 27
    assert _registered_workflows(WorkflowRegistry()) == list(LOCAL_TEST_WORKFLOWS)
    assert configured_scheduler_identities() == ("fixture.cleanup", "builtin.backup")


def test_empty_and_false_ini_settings_suppress_legacy_enablement(custom_ini, monkeypatch):
    """Explicitly empty selection/domain and a false feature flag must stay disabled."""
    custom_ini("""
[temporal]
user_domain =
[temporal.worker]
enable_local_test_workflows = false
[temporal.scheduler]
enabled_schedulers =
""")
    monkeypatch.setenv("NV_CONFIG_MANAGER_DOMAIN", "legacy.example.com")
    monkeypatch.setenv("NVCM_ENABLE_LOCAL_TEST_WORKFLOWS", "true")
    monkeypatch.setenv("NVCM_ENABLED_SCHEDULERS", "builtin.backup")

    assert default_user_domain() is None
    assert _registered_workflows(WorkflowRegistry()) == []
    assert configured_scheduler_identities() == ()


@pytest.mark.parametrize(
    ("concurrency", "timeout", "expected"),
    [("invalid", "invalid", (8, 120)), ("0", "-2", (1, 1))],
)
def test_ini_template_limits_keep_validation(custom_ini, concurrency, timeout, expected):
    custom_ini(f"[render.template_updater]\nconcurrency = {concurrency}\nlock_timeout = {timeout}")

    assert (template_update_concurrency(), template_update_lock_timeout()) == expected


def test_duplicate_ini_scheduler_selection_is_rejected(custom_ini):
    custom_ini("[temporal.scheduler]\nenabled_schedulers = fixture.cleanup,fixture.cleanup")

    with pytest.raises(SchedulerHostError, match="more than once"):
        configured_scheduler_identities()
