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
"""Tests for downloading workflow tech-support bundles."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from nv_config_manager.temporal.api import workflow_v1


def _configure_authorized_download(monkeypatch: pytest.MonkeyPatch, content: bytes | None):
    handle = MagicMock()
    client = MagicMock()
    client.get_workflow_handle.return_value = handle
    cache = MagicMock()
    cache.get = AsyncMock(return_value=content)

    monkeypatch.setattr(workflow_v1, "get_client", AsyncMock(return_value=client))
    monkeypatch.setattr(workflow_v1, "is_authorized", AsyncMock(return_value=True))
    monkeypatch.setattr(
        workflow_v1.RedisClient,
        "from_config",
        MagicMock(return_value=cache),
    )
    monkeypatch.setattr(workflow_v1, "load_config", MagicMock())
    return cache


async def test_download_tech_support_uses_shared_bundle_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The API consumer reads the same package-owned key the producer writes."""
    cache = _configure_authorized_download(monkeypatch, b"bundle bytes")
    key_builder = MagicMock(return_value="shared-tech-support-key")
    monkeypatch.setattr(workflow_v1, "tech_support_key", key_builder)

    response = await workflow_v1.download_tech_support(
        "workflow: 42",
        "switch: 01",
        MagicMock(),
    )

    key_builder.assert_called_once_with("workflow: 42", "switch: 01")
    cache.get.assert_awaited_once_with("shared-tech-support-key", deserialize=False)
    assert response.body == b"bundle bytes"
    assert response.media_type == "application/gzip"
    assert response.headers["content-disposition"] == (
        'attachment; filename="tech-support_switch: 01.tar.gz"'
    )


async def test_download_tech_support_preserves_missing_bundle_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An absent or expired bundle retains its existing 404 payload."""
    cache = _configure_authorized_download(monkeypatch, None)

    with pytest.raises(HTTPException) as raised:
        await workflow_v1.download_tech_support("workflow-1", "switch-1", MagicMock())

    assert raised.value.status_code == 404
    assert raised.value.detail == (
        "Tech-support bundle for 'switch-1' not found "
        "(key=tech_support:workflow-1:switch-1). It may have expired."
    )
    cache.get.assert_awaited_once_with(
        "tech_support:workflow-1:switch-1",
        deserialize=False,
    )
