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
"""Airgapped uv downloads fail closed before extraction on checksum errors."""

from __future__ import annotations

import hashlib
import io
import os
import re
import subprocess
import tarfile
from pathlib import Path

import pytest

_POLICY = Path(__file__).resolve().parents[3] / "deploy/helm/files/python-tooling.sh"
_SCRIPT = Path(__file__).resolve().parents[3] / "deploy/airgapped/create-airgapped.sh"


@pytest.mark.parametrize(
    "arch,triple", [("amd64", "x86_64-unknown-linux-gnu"), ("arm64", "aarch64-unknown-linux-gnu")]
)
@pytest.mark.parametrize("corrupt", [False, True])
def test_airgapped_uv_is_verified_before_extraction(
    tmp_path: Path,
    arch: str,
    triple: str,
    corrupt: bool,
) -> None:
    archive = tmp_path / "fixture.tar.gz"
    binary = b"fixture uv binary"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo(f"uv-{triple}/uv")
        info.size = len(binary)
        tar.addfile(info, io.BytesIO(binary))
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    if corrupt:
        archive.write_bytes(b"corrupted archive")
    source = _SCRIPT.read_text()
    function = (
        "download_installer_uv() {"
        + source.split("download_installer_uv() {", 1)[1].split("\n}\n", 1)[0]
        + "\n}\n"
    )
    # Substitute a local release fixture's expected digest; exercise real checksum and tar tools.
    policy = re.sub(
        r"NVCM_UV_(AMD64|ARM64)_SHA256=[0-9a-f]{64}",
        lambda match: f"NVCM_UV_{match[1]}_SHA256={checksum}",
        _POLICY.read_text(),
    )
    destination = tmp_path / "installer"
    destination.mkdir()
    driver = (
        """
set -eu
log_info() { :; }
log_success() { :; }
log_error() { echo "$*" >&2; }
curl() {
    printf '%s\n' "$4" > "$UV_TEST_URL"
    cp "$UV_TEST_ARCHIVE" "$3"
}
"""
        + policy
        + "\n"
        + function
        + '\ndownload_installer_uv "$1" "$2"\n'
    )
    result = subprocess.run(
        ["bash", "-c", driver, "test", str(destination), arch],
        env={**os.environ, "UV_TEST_ARCHIVE": str(archive), "UV_TEST_URL": str(tmp_path / "url")},
        capture_output=True,
        text=True,
    )
    assert (
        (tmp_path / "url").read_text().strip()
        == f"https://github.com/astral-sh/uv/releases/download/0.13.4/uv-{triple}.tar.gz"
    )
    packaged = destination / f"uv-{arch}"
    if corrupt:
        assert result.returncode != 0
        assert not packaged.exists()
        assert "checksum verification failed" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert packaged.read_bytes() == binary
        assert os.access(packaged, os.X_OK)
    assert not list(destination.glob(".uv-*"))


def _shell_function(name: str) -> str:
    return (
        name
        + "() {"
        + _SCRIPT.read_text().split(name + "() {", 1)[1].split("\n}\n", 1)[0]
        + "\n}\n"
    )


def test_pip_download_keeps_project_version_enforcement(tmp_path: Path) -> None:
    log = tmp_path / "uv-calls"
    driver = r"""
set -eu
uv() { printf '%s\n' "$*" > "$UV_TEST_CALLS"; }
"""
    driver += _POLICY.read_text() + "\n" + _shell_function("pip_download")
    result = subprocess.run(
        ["bash", "-c", driver + "\npip_download fixture.whl\n"],
        env={**os.environ, "REPO_ROOT": str(tmp_path), "UV_TEST_CALLS": str(log)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines() == [
        f"run --project {tmp_path}/installer --no-sync --with pip==26.2.2 python -m pip download fixture.whl"
    ]
