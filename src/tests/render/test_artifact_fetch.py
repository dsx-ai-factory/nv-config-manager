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
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nv_config_manager.render.artifact_fetch import (
    ArtifactFetchError,
    _plugin_install_dir,
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
def test_fetch_verify_extract_zip(mock_get: MagicMock, tmp_path: Path) -> None:
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
def test_fetch_verify_extract_tar(mock_get: MagicMock, tmp_path: Path) -> None:
    files = {"plugin.yaml": b"id: site-overrides\n"}
    content = _make_tar(files)
    checksum = _checksum_for(content)
    mock_get.return_value = MagicMock(status_code=200, content=content)

    fetch_and_verify_artifact(
        "https://tp/internal/artifacts/x", "sha256:content-digest", checksum, str(tmp_path)
    )

    assert (tmp_path / "plugin.yaml").read_bytes() == files["plugin.yaml"]


@patch("nv_config_manager.render.artifact_fetch.requests.get")
def test_fetch_sends_bearer_token_when_provided(mock_get: MagicMock, tmp_path: Path) -> None:
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
def test_fetch_raises_on_checksum_mismatch(mock_get: MagicMock, tmp_path: Path) -> None:
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
def test_fetch_raises_on_non_200(mock_get: MagicMock, tmp_path: Path) -> None:
    mock_get.return_value = MagicMock(status_code=404, content=b"")
    with pytest.raises(ArtifactFetchError, match="HTTP 404"):
        fetch_and_verify_artifact(
            "https://tp/internal/artifacts/x",
            "sha256:content-digest",
            "sha256:" + "0" * 64,
            str(tmp_path),
        )


@patch("nv_config_manager.render.artifact_fetch.requests.get")
def test_fetch_rejects_archive_member_path_traversal(mock_get: MagicMock, tmp_path: Path) -> None:
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
def test_fetch_rejects_non_archive_content(mock_get: MagicMock, tmp_path: Path) -> None:
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
def test_cli_main_noop_when_nothing_active(
    mock_store_cls: MagicMock, mock_fetch: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RENDER_ACTIVATION_CONFIGMAP", "cm")
    monkeypatch.setenv("RENDER_ACTIVATION_NAMESPACE", "ns")
    mock_store_cls.return_value.get.return_value = {"active_artifacts": []}

    cli_main()

    mock_fetch.assert_not_called()


def _artifact(plugin_id: str) -> dict:
    return {
        "plugin_id": plugin_id,
        "digest": f"sha256:{plugin_id}",
        "archive_checksum": f"sha256:{plugin_id}-raw",
        "url": f"https://tp/internal/artifacts/{plugin_id}",
    }


@patch("nv_config_manager.render.artifact_fetch.fetch_and_verify_artifact")
@patch("nv_config_manager.render.artifact_fetch.ActivationStateStore")
def test_a_pod_restarted_for_an_activation_installs_the_new_set_not_the_old_one(
    mock_store_cls: MagicMock, mock_fetch: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The restart is triggered after the new set is recorded as in flight, so every restarted
    Deployment installs it. (Previously the consumers restarted first and installed the old set.)"""
    monkeypatch.setenv("RENDER_ACTIVATION_CONFIGMAP", "cm")
    monkeypatch.setenv("RENDER_ACTIVATION_NAMESPACE", "ns")
    mock_store_cls.return_value.get.return_value = {
        "active_digest": "sha256:old",
        "active_artifacts": [_artifact("old-plugin")],
        "in_progress_digest": "sha256:new",
        "in_progress_artifacts": [_artifact("new-plugin")],
    }

    cli_main()

    assert [call.args[3] for call in mock_fetch.call_args_list] == [
        "/opt/plugins/_dynamic/new-plugin"
    ]


@patch("nv_config_manager.render.artifact_fetch.fetch_and_verify_artifact")
@patch("nv_config_manager.render.artifact_fetch.ActivationStateStore")
def test_after_a_failed_activation_a_restarted_pod_goes_back_to_the_active_set(
    mock_store_cls: MagicMock, mock_fetch: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RENDER_ACTIVATION_CONFIGMAP", "cm")
    monkeypatch.setenv("RENDER_ACTIVATION_NAMESPACE", "ns")
    mock_store_cls.return_value.get.return_value = {
        "active_digest": "sha256:old",
        "active_artifacts": [_artifact("old-plugin")],
        "in_progress_digest": None,
        "in_progress_artifacts": [],
    }

    cli_main()

    assert [call.args[3] for call in mock_fetch.call_args_list] == [
        "/opt/plugins/_dynamic/old-plugin"
    ]


@patch("nv_config_manager.render.artifact_fetch.fetch_and_verify_artifact")
@patch("nv_config_manager.render.artifact_fetch.ActivationStateStore")
def test_cli_main_fetches_every_active_artifact(
    mock_store_cls: MagicMock, mock_fetch: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
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
        "/opt/plugins/_dynamic/a",
        "tok",
    )
    mock_fetch.assert_any_call(
        "https://tp/internal/artifacts/bbbb",
        "sha256:bbbb",
        "sha256:bbbb-raw",
        "/opt/plugins/_dynamic/b",
        "tok",
    )


@patch("nv_config_manager.render.artifact_fetch.fetch_and_verify_artifact")
@patch("nv_config_manager.render.artifact_fetch.ActivationStateStore")
def test_cli_main_exits_nonzero_on_fetch_failure(
    mock_store_cls: MagicMock, mock_fetch: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
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


# --- one folder per plugin, and a safe plugin id ---------------------------------------------


def test_each_plugin_is_extracted_into_its_own_folder() -> None:
    assert (
        _plugin_install_dir("/opt/plugins", "site-overrides")
        == "/opt/plugins/_dynamic/site-overrides"
    )
    assert _plugin_install_dir("/opt/plugins", "a.b_c-1") == "/opt/plugins/_dynamic/a.b_c-1"


@pytest.mark.parametrize("bad", ["", "..", "../etc", "a/b", "/abs", ".hidden", "a..b", None, 5])
def test_unsafe_plugin_ids_are_refused(bad: object) -> None:
    with pytest.raises(ArtifactFetchError, match="unsafe plugin id"):
        _plugin_install_dir("/opt/plugins", bad)


@patch("nv_config_manager.render.artifact_fetch.fetch_and_verify_artifact")
@patch("nv_config_manager.render.artifact_fetch.ActivationStateStore")
def test_cli_main_refuses_an_unsafe_plugin_id_without_fetching(
    mock_store_cls: MagicMock, mock_fetch: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RENDER_ACTIVATION_CONFIGMAP", "cm")
    monkeypatch.setenv("RENDER_ACTIVATION_NAMESPACE", "ns")
    mock_store_cls.return_value.get.return_value = {
        "active_artifacts": [
            {
                "plugin_id": "../escape",
                "digest": "sha256:x",
                "archive_checksum": "sha256:y",
                "url": "https://tp/x",
            }
        ]
    }

    with pytest.raises(SystemExit) as exc:
        cli_main()

    assert exc.value.code == 1
    mock_fetch.assert_not_called()


# --- only the selected set is left installed (review finding) -----------------------------------------


def _install_env(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setenv("RENDER_ACTIVATION_CONFIGMAP", "cm")
    monkeypatch.setenv("RENDER_ACTIVATION_NAMESPACE", "ns")
    monkeypatch.setenv("RENDER_PLUGIN_INSTALL_PATH", str(root))


@patch("nv_config_manager.render.artifact_fetch.fetch_and_verify_artifact")
@patch("nv_config_manager.render.artifact_fetch.ActivationStateStore")
def test_an_empty_selection_removes_plugins_left_by_an_earlier_attempt(
    mock_store_cls: MagicMock,
    mock_fetch: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # A retried init container in the same pod: the failed attempt's files are still on the volume, and
    # the set to install is now empty (nothing active). The loader must not find them.
    _install_env(monkeypatch, tmp_path)
    (tmp_path / "_dynamic" / "from-failed-attempt").mkdir(parents=True)
    (tmp_path / "_dynamic" / "from-failed-attempt" / "plugin.yaml").write_text("kind: Plugin\n")
    mock_store_cls.return_value.get.return_value = {"active_artifacts": []}

    cli_main()

    assert not (tmp_path / "_dynamic").exists()
    mock_fetch.assert_not_called()


@patch("nv_config_manager.render.artifact_fetch.fetch_and_verify_artifact")
@patch("nv_config_manager.render.artifact_fetch.ActivationStateStore")
def test_only_the_selected_set_is_left_in_the_dynamic_folder(
    mock_store_cls: MagicMock,
    mock_fetch: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_env(monkeypatch, tmp_path)
    leftover = tmp_path / "_dynamic" / "from-failed-attempt"
    leftover.mkdir(parents=True)
    (leftover / "plugin.yaml").write_text("kind: Plugin\n")
    mock_store_cls.return_value.get.return_value = {"active_artifacts": [_artifact("selected")]}

    def fake_fetch(url: str, digest: str, checksum: str, dest: Path, token: str | None) -> None:
        (tmp_path / "_dynamic" / "selected").mkdir(parents=True, exist_ok=True)
        (tmp_path / "_dynamic" / "selected" / "plugin.yaml").write_text("kind: Plugin\n")

    mock_fetch.side_effect = fake_fetch

    cli_main()

    assert sorted(p.name for p in (tmp_path / "_dynamic").iterdir()) == ["selected"]


@patch("nv_config_manager.render.artifact_fetch.fetch_and_verify_artifact")
@patch("nv_config_manager.render.artifact_fetch.ActivationStateStore")
def test_static_plugins_outside_the_dynamic_folder_are_never_touched(
    mock_store_cls: MagicMock,
    mock_fetch: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_env(monkeypatch, tmp_path)
    (tmp_path / "static-plugin").mkdir()
    (tmp_path / "static-plugin" / "plugin.yaml").write_text("kind: Plugin\n")
    (tmp_path / "_dynamic" / "old").mkdir(parents=True)
    mock_store_cls.return_value.get.return_value = {"active_artifacts": []}

    cli_main()

    assert (tmp_path / "static-plugin" / "plugin.yaml").exists()
    assert not (tmp_path / "_dynamic").exists()
