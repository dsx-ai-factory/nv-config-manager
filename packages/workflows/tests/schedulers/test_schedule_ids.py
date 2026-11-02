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

import pytest

from nv_config_manager_workflows import scheduler_identity
from nv_config_manager_workflows.registration import validation
from nv_config_manager_workflows.schedulers.schedule_ids import (
    SCHEDULE_ID_SEPARATOR,
    owns_schedule_id,
    schedule_id,
)


def test_separator_is_colon() -> None:
    assert SCHEDULE_ID_SEPARATOR == ":"


def test_schedule_id_joins_identity_and_key() -> None:
    assert schedule_id("acme.cleanup", "device-1") == "acme.cleanup:device-1"


def test_schedule_id_accepts_nested_identity() -> None:
    assert schedule_id("acme.ops.nightly-sync", "k") == "acme.ops.nightly-sync:k"


def test_key_containing_separator_round_trips() -> None:
    sid = schedule_id("acme.cleanup", "site:rack-1")

    assert sid == "acme.cleanup:site:rack-1"
    assert owns_schedule_id("acme.cleanup", sid)


@pytest.mark.parametrize("identity", ["Acme.x", "acme", "acme.", "", ".acme.x", "acme.1x"])
def test_schedule_id_rejects_invalid_identity(identity: str) -> None:
    with pytest.raises(ValueError, match="Scheduler identity"):
        schedule_id(identity, "k")


def test_schedule_id_rejects_empty_key() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        schedule_id("acme.cleanup", "")


def test_schedule_id_rejects_key_with_slash() -> None:
    with pytest.raises(ValueError, match="Temporal's HTTP API cannot address"):
        schedule_id("acme.cleanup", "site/rack-1")


@pytest.mark.parametrize("key", ["device-1", "k", "site:rack-1"])
def test_owns_schedule_id_accepts_own_ids(key: str) -> None:
    assert owns_schedule_id("acme.cleanup", schedule_id("acme.cleanup", key))


# Identities that resemble the built-in "backup-" prefix still produce
# "<identity>:<key>" IDs that their scheduler owns.
@pytest.mark.parametrize("identity", ["backup.sync", "backups.sync", "acme.backup-sync"])
def test_backup_like_identity_owns_its_schedule_ids(identity: str) -> None:
    sid = schedule_id(identity, "dev1")

    assert sid == f"{identity}:dev1"
    assert owns_schedule_id(identity, sid)


@pytest.mark.parametrize(
    "candidate",
    [
        "acme.cleanup-v2:k",
        "acme.cleanup:",
        "acme.cleanup",
        "backup-11111111-1111-1111-1111-111111111111",
        "other.cleanup:device-1",
        "acme.cleanup.extra:k",
        "",
    ],
)
def test_owns_schedule_id_rejects_foreign_ids(candidate: str) -> None:
    assert not owns_schedule_id("acme.cleanup", candidate)


def test_backup_plugin_does_not_own_builtin_backup_ids() -> None:
    assert not owns_schedule_id("backup.sync", "backup-11111111-1111-1111-1111-111111111111")


def test_registry_validation_shares_identity_patterns() -> None:
    assert validation.SCHEDULER_IDENTITY_PATTERN is scheduler_identity.SCHEDULER_IDENTITY_PATTERN
    assert (
        validation.SCHEDULER_IDENTITY_SEGMENT_PATTERN
        is scheduler_identity.SCHEDULER_IDENTITY_SEGMENT_PATTERN
    )
