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
"""Tests for artifact_fetch.py: fetch+checksum-verify+extract, and the
cli_main() init-container entrypoint. Network calls (requests.get) and
Kubernetes calls (via ActivationStateStore) are mocked -- no live
Template Preview instance or cluster in this test environment.

The value verified against a download is archive_checksum (a hash of the
raw archive bytes), not digest (a canonicalized-tree content digest that
is never a hash of any byte sequence that crosses the wire) -- see
fetch_and_verify_artifact's docstring."""

from __future__ import annotations

import hashlib
import io
import tarfile
import zipfile
from unittest.mock import MagicMock, patch

import pytest

from nv_config_manager.render.artifact_fetch import (
    ArtifactFetchError,
    cli_main,
    fetch_and_verify_artifact,
)


def _checksum_for(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, content in files.items():
            zf.writestr(path, content)
    return buf.getvalue()


def _make_tar(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for path, content in files.items():
            info = tarfile.TarInfo(name=path)
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


# --- fetch_and_verify_artifact -------------------------------------------------


@patch("nv_config_manager.render.artifact_fetch.requests.get")
def test_fetch_verify_extract_zip(mock_get, tmp_path):
    files = {"plugin.yaml": b"id: site-overrides\n", "templates/base.j2": b"{{ hostname }}\n"}
    content = _make_zip(files)
    checksum = _checksum_for(content)
    mock_get.return_value = MagicMock(status_code=200, content=content)

    fetch_and_verify_artifact(
        "https://tp/internal/artifacts/x", "sha256:content-digest", checksum, str(tmp_path)
    )

    assert (tmp_path / "plugin.yaml").read_bytes() == files["plugin.yaml"]
    assert (tmp_path / "templates/base.j2").read_bytes() == files["templates/base.j2"]


@patch("nv_config_manager.render.artifact_fetch.requests.get")
def test_fetch_verify_extract_tar(mock_get, tmp_path):
    files = {"plugin.yaml": b"id: site-overrides\n"}
    content = _make_tar(files)
    checksum = _checksum_for(content)
    mock_get.return_value = MagicMock(status_code=200, content=content)

    fetch_and_verify_artifact(
        "https://tp/internal/artifacts/x", "sha256:content-digest", checksum, str(tmp_path)
    )

    assert (tmp_path / "plugin.yaml").read_bytes() == files["plugin.yaml"]


@patch("nv_config_manager.render.artifact_fetch.requests.get")
def test_fetch_sends_bearer_token_when_provided(mock_get, tmp_path):
    content = _make_zip({"a.txt": b"x"})
    checksum = _checksum_for(content)
    mock_get.return_value = MagicMock(status_code=200, content=content)

    fetch_and_verify_artifact(
        "https://tp/internal/artifacts/x",
        "sha256:content-digest",
        checksum,
        str(tmp_path),
        token="tok",
    )

    _, kwargs = mock_get.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer tok"


@patch("nv_config_manager.render.artifact_fetch.requests.get")
def test_fetch_raises_on_checksum_mismatch(mock_get, tmp_path):
    content = _make_zip({"a.txt": b"x"})
    mock_get.return_value = MagicMock(status_code=200, content=content)

    with pytest.raises(ArtifactFetchError, match="Checksum mismatch"):
        fetch_and_verify_artifact(
            "https://tp/internal/artifacts/x",
            "sha256:content-digest",
            "sha256:" + "0" * 64,
            str(tmp_path),
        )


@patch("nv_config_manager.render.artifact_fetch.requests.get")
def test_fetch_raises_on_non_200(mock_get, tmp_path):
    mock_get.return_value = MagicMock(status_code=404, content=b"")
    with pytest.raises(ArtifactFetchError, match="HTTP 404"):
        fetch_and_verify_artifact(
            "https://tp/internal/artifacts/x",
            "sha256:content-digest",
            "sha256:" + "0" * 64,
            str(tmp_path),
        )


@patch("nv_config_manager.render.artifact_fetch.requests.get")
def test_fetch_rejects_archive_member_path_traversal(mock_get, tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../../etc/passwd", b"nope")
    content = buf.getvalue()
    checksum = _checksum_for(content)
    mock_get.return_value = MagicMock(status_code=200, content=content)

    with pytest.raises(ArtifactFetchError, match="unsafe archive member"):
        fetch_and_verify_artifact(
            "https://tp/internal/artifacts/x", "sha256:content-digest", checksum, str(tmp_path)
        )


@patch("nv_config_manager.render.artifact_fetch.requests.get")
def test_fetch_rejects_non_archive_content(mock_get, tmp_path):
    content = b"not an archive at all"
    checksum = _checksum_for(content)
    mock_get.return_value = MagicMock(status_code=200, content=content)

    with pytest.raises(ArtifactFetchError):
        fetch_and_verify_artifact(
            "https://tp/internal/artifacts/x", "sha256:content-digest", checksum, str(tmp_path)
        )


# --- cli_main --------------------------------------------------------------


@patch("nv_config_manager.render.artifact_fetch.fetch_and_verify_artifact")
@patch("nv_config_manager.render.artifact_fetch.ActivationStateStore")
def test_cli_main_noop_when_nothing_active(mock_store_cls, mock_fetch, monkeypatch):
    monkeypatch.setenv("RENDER_ACTIVATION_CONFIGMAP", "cm")
    monkeypatch.setenv("RENDER_ACTIVATION_NAMESPACE", "ns")
    mock_store_cls.return_value.get.return_value = {"active_artifacts": []}

    cli_main()

    mock_fetch.assert_not_called()


@patch("nv_config_manager.render.artifact_fetch.fetch_and_verify_artifact")
@patch("nv_config_manager.render.artifact_fetch.ActivationStateStore")
def test_cli_main_fetches_every_active_artifact(mock_store_cls, mock_fetch, monkeypatch):
    monkeypatch.setenv("RENDER_ACTIVATION_CONFIGMAP", "cm")
    monkeypatch.setenv("RENDER_ACTIVATION_NAMESPACE", "ns")
    monkeypatch.setenv("TEMPLATE_PREVIEW_ARTIFACT_TOKEN", "tok")
    mock_store_cls.return_value.get.return_value = {
        "active_artifacts": [
            {
                "plugin_id": "a",
                "digest": "sha256:aaaa",
                "archive_checksum": "sha256:aaaa-raw",
                "url": "https://tp/internal/artifacts/aaaa",
            },
            {
                "plugin_id": "b",
                "digest": "sha256:bbbb",
                "archive_checksum": "sha256:bbbb-raw",
                "url": "https://tp/internal/artifacts/bbbb",
            },
        ]
    }

    cli_main()

    assert mock_fetch.call_count == 2
    mock_fetch.assert_any_call(
        "https://tp/internal/artifacts/aaaa",
        "sha256:aaaa",
        "sha256:aaaa-raw",
        "/opt/plugins",
        "tok",
    )
    mock_fetch.assert_any_call(
        "https://tp/internal/artifacts/bbbb",
        "sha256:bbbb",
        "sha256:bbbb-raw",
        "/opt/plugins",
        "tok",
    )


@patch("nv_config_manager.render.artifact_fetch.fetch_and_verify_artifact")
@patch("nv_config_manager.render.artifact_fetch.ActivationStateStore")
def test_cli_main_exits_nonzero_on_fetch_failure(mock_store_cls, mock_fetch, monkeypatch):
    monkeypatch.setenv("RENDER_ACTIVATION_CONFIGMAP", "cm")
    monkeypatch.setenv("RENDER_ACTIVATION_NAMESPACE", "ns")
    mock_store_cls.return_value.get.return_value = {
        "active_artifacts": [
            {
                "plugin_id": "a",
                "digest": "sha256:aaaa",
                "archive_checksum": "sha256:aaaa-raw",
                "url": "https://tp/x",
            }
        ]
    }
    mock_fetch.side_effect = ArtifactFetchError("boom")

    with pytest.raises(SystemExit) as exc_info:
        cli_main()

    assert exc_info.value.code == 1
