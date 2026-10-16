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
"""The legacy scheduler module path re-exports the moved scheduler host entry point."""

from nv_config_manager.temporal.ngc.schedulers import backup
from nv_config_manager.temporal.scheduler import main as scheduler_main
from nv_config_manager_workflows.schedulers.backup import (
    BackupScheduler as CanonicalBackupScheduler,
)


def test_legacy_backup_scheduler_is_the_canonical_package_class() -> None:
    assert backup.BackupScheduler is CanonicalBackupScheduler


def test_legacy_main_is_the_scheduler_host_entry_point() -> None:
    assert backup.main is scheduler_main.main


def test_legacy_module_exports_only_compatibility_names() -> None:
    assert backup.__all__ == ["BackupScheduler", "main"]
