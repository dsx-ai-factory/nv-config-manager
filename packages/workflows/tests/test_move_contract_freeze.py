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
"""Identifiers frozen by the move of the lock chain and codec into this package.

Moving code must not move these strings. They are matched against data that
already exists outside the process -- Temporal workflow histories, Redis keys
written by running workers -- so a rename here breaks in-flight executions
rather than failing a build. Reason: GNICFD W5.
"""

from __future__ import annotations

from nv_config_manager_workflows.activities import REGISTERED_COMMON_ACTIVITIES
from nv_config_manager_workflows.converter import COMPRESSION_ENCODING
from nv_config_manager_workflows.decorators.workflow import _WORKFLOW_LOCK_PATCH_ID
from nv_config_manager_workflows.metadata.lock import _LOCK_KEY_PREFIX
from nv_config_manager_workflows.mixins.archive import PUBLISH_NATS_ACTIVITY_NAME
from nv_config_manager_workflows.registration import activity_name


def test_compression_encoding_is_frozen() -> None:
    """Payloads in existing histories carry this marker; decode keys off it."""
    assert COMPRESSION_ENCODING == "binary/gzip"


def test_lock_key_prefix_is_frozen() -> None:
    """Running workers hold Redis keys under this prefix."""
    assert _LOCK_KEY_PREFIX == "wf-lock"


def test_workflow_lock_patch_id_is_frozen() -> None:
    """Executions already past this patch replay against the same identifier."""
    assert _WORKFLOW_LOCK_PATCH_ID == "nvcm-workflow-lock-v1"


def test_lock_activity_names_are_frozen() -> None:
    """Activity type names are matched as strings when replaying a history."""
    names = sorted((activity_name(fn) for fn in REGISTERED_COMMON_ACTIVITIES), key=str)

    assert names == [
        "acquire_workflow_lock",
        "release_workflow_lock",
        "renew_workflow_lock",
    ]


def test_publish_nats_activity_name_is_frozen() -> None:
    """ArchiveMixin dispatches on this string; the service registers the publisher."""
    assert PUBLISH_NATS_ACTIVITY_NAME == "publish_nats"
