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
    function = re.sub(r'uv_sha256="[0-9a-f]{64}"', f'uv_sha256="{checksum}"', function)
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


@pytest.mark.parametrize(
    "version,allowed",
    [
        ("0.6.10", False),
        ("0.13.3", False),
        ("0.13.4-dev", False),
        ("invalid", False),
        ("unavailable", False),
        ("0.13.4", True),
        ("0.13.5", True),
        ("0.14.0", True),
        ("1.0.0", True),
    ],
)
def test_pip_download_requires_patched_host_uv_before_network(
    tmp_path: Path, version: str, allowed: bool
) -> None:
    log = tmp_path / "uv-calls"
    driver = r"""
set -eu
log_error() { echo "$*" >&2; }
uv() {
    printf '%s\n' "$*" >> "$UV_TEST_CALLS"
    if [ "$1" = --version ]; then
        [ "$UV_TEST_VERSION" != unavailable ] || return 127
        printf 'uv %s (test build)\n' "$UV_TEST_VERSION"
    fi
}
"""
    driver += _shell_function("require_patched_uv") + _shell_function("pip_download")
    result = subprocess.run(
        ["bash", "-c", driver + "\npip_download fixture.whl\n"],
        env={**os.environ, "UV_TEST_VERSION": version, "UV_TEST_CALLS": str(log)},
        capture_output=True,
        text=True,
    )
    calls = log.read_text().splitlines()
    if allowed:
        assert result.returncode == 0, result.stderr
        assert calls == [
            "--version",
            "run --no-project --with pip==26.2.2 python -m pip download fixture.whl",
        ]
    else:
        assert result.returncode != 0
        assert calls == ["--version"]
        assert "uv >=0.13.4 is required" in result.stderr


def test_installer_packaging_rejects_old_uv_before_building(tmp_path: Path) -> None:
    installer = tmp_path / "installer"
    installer.mkdir()
    (installer / "pyproject.toml").touch()
    log = tmp_path / "uv-calls"
    driver = r"""
set -eu
log_info() { :; }
log_error() { echo "$*" >&2; }
uv() {
    printf '%s\n' "$*" >> "$UV_TEST_CALLS"
    printf 'uv 0.13.3\n'
}
"""
    driver += _shell_function("require_patched_uv") + _shell_function("package_installer")
    result = subprocess.run(
        ["bash", "-c", driver + "\npackage_installer\n"],
        env={**os.environ, "REPO_ROOT": str(tmp_path), "UV_TEST_CALLS": str(log)},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert log.read_text().splitlines() == ["--version"]
    assert "uv >=0.13.4 is required" in result.stderr
