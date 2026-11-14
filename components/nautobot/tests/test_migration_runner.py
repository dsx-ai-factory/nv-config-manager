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
"""Tests for nv_config_manager_jobs.migration_runner."""

from __future__ import annotations

import subprocess
import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest


class FakeCursor:
    """Cursor that tracks the advisory lock and the init state table."""

    def __init__(self, recorded: str | None = None, lock_free: bool = True):
        self.recorded = recorded
        self.lock_free = lock_free
        self.lock_held = False
        self._result = None

    def execute(self, sql, params=None):
        if "pg_try_advisory_lock" in sql:
            self._result = (self.lock_free,)
            self.lock_held = self.lock_free
        elif "pg_advisory_unlock" in sql:
            self.lock_held = False
            self._result = (True,)
        elif sql.lstrip().startswith("SELECT fingerprint"):
            self._result = (self.recorded,) if self.recorded is not None else None
        elif sql.lstrip().startswith("INSERT INTO nv_config_manager_init_state"):
            self.recorded = params[1]

    def fetchone(self):
        return self._result


FINGERPRINT = "a1b2c3d4e5f6a7b8"


@pytest.fixture()
def runner(monkeypatch):
    sys.modules["django"].setup = MagicMock()
    sys.modules["django.db"].connection = MagicMock()
    for name in ("django.db.migrations", "django.db.migrations.executor"):
        monkeypatch.setitem(sys.modules, name, ModuleType(name))
    sys.modules["django.db.migrations.executor"].MigrationExecutor = MagicMock()

    import nv_config_manager_jobs.migration_runner as mod

    monkeypatch.setenv("NAUTOBOT_INIT_FINGERPRINT", FINGERPRINT)
    commands: list[list[str]] = []
    monkeypatch.setattr(mod, "run_cmd", lambda cmd, check=True: commands.append(cmd) or 0)
    monkeypatch.setattr(mod, "set_superuser_password", MagicMock())
    monkeypatch.setattr(mod, "create_api_token", MagicMock())
    monkeypatch.setattr(mod, "run_bootstrap_job", MagicMock())
    monkeypatch.setattr(mod, "has_pending_migrations", MagicMock(return_value=False))
    mod.commands = commands
    return mod


def _use_cursor(mod, cursor):
    mod.connection.cursor = MagicMock(return_value=cursor)


def _ran(mod, subcommand: str) -> bool:
    return any(cmd[:2] == ["nautobot-server", subcommand] for cmd in mod.commands)


class TestMain:
    def test_first_run_migrates_and_records_fingerprint(self, runner):
        cursor = FakeCursor()
        _use_cursor(runner, cursor)

        assert runner.main() == 0

        assert _ran(runner, "migrate")
        assert _ran(runner, "post_upgrade")
        assert cursor.recorded == FINGERPRINT
        assert not cursor.lock_held
        runner.run_bootstrap_job.assert_called_once()

    def test_rolling_update_pod_skips_completed_init(self, runner):
        cursor = FakeCursor(recorded=FINGERPRINT)
        _use_cursor(runner, cursor)

        assert runner.main() == 0

        assert not _ran(runner, "migrate")
        assert not _ran(runner, "post_upgrade")
        assert _ran(runner, "createsuperuser")
        runner.set_superuser_password.assert_called_once()
        runner.create_api_token.assert_called_once()
        runner.run_bootstrap_job.assert_not_called()
        assert not cursor.lock_held

    def test_changed_fingerprint_reruns_init(self, runner):
        cursor = FakeCursor(recorded="previous-deployment")
        _use_cursor(runner, cursor)

        assert runner.main() == 0

        assert _ran(runner, "migrate")
        assert _ran(runner, "post_upgrade")
        assert cursor.recorded == FINGERPRINT

    def test_pending_migrations_rerun_init_even_with_matching_fingerprint(self, runner):
        runner.has_pending_migrations.return_value = True
        cursor = FakeCursor(recorded=FINGERPRINT)
        _use_cursor(runner, cursor)

        assert runner.main() == 0

        assert _ran(runner, "migrate")
        assert _ran(runner, "post_upgrade")

    def test_missing_fingerprint_always_runs_init(self, runner, monkeypatch):
        monkeypatch.delenv("NAUTOBOT_INIT_FINGERPRINT")
        cursor = FakeCursor(recorded="")
        _use_cursor(runner, cursor)

        assert runner.main() == 0

        assert _ran(runner, "migrate")
        assert _ran(runner, "post_upgrade")
        assert cursor.recorded == ""

    def test_failed_post_upgrade_does_not_record_fingerprint(self, runner, monkeypatch):
        def run_cmd(cmd, check=True):
            if cmd[:2] == ["nautobot-server", "post_upgrade"]:
                raise subprocess.CalledProcessError(1, cmd)
            return 0

        monkeypatch.setattr(runner, "run_cmd", run_cmd)
        cursor = FakeCursor()
        _use_cursor(runner, cursor)

        with pytest.raises(subprocess.CalledProcessError):
            runner.main()

        assert cursor.recorded is None
        assert not cursor.lock_held
        runner.run_bootstrap_job.assert_not_called()
