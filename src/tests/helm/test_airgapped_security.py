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
"""Airgapped downloads enforce host tooling and verify Python before extraction."""

from __future__ import annotations

import hashlib
import io
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

_POLICY = Path(__file__).resolve().parents[3] / "deploy/helm/files/python-tooling.sh"
_SCRIPT = Path(__file__).resolve().parents[3] / "deploy/airgapped/create-airgapped.sh"
_RUNTIME_HASHES = _SCRIPT.with_name("python-runtimes.sha256")


def _shell_function(name: str) -> str:
    return (
        name
        + "() {"
        + _SCRIPT.read_text().split(name + "() {", 1)[1].split("\n}\n", 1)[0]
        + "\n}\n"
    )


def _download_python(
    tmp_path: Path, triple: str, failure: str = ""
) -> tuple[subprocess.CompletedProcess[str], Path]:
    archive = tmp_path / "fixture.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo(
            "python/README" if failure == "missing-binary" else "python/bin/python3"
        )
        payload = b"#!/bin/sh\nexit 0\n"
        info.size = len(payload)
        info.mode = 0o755
        tar.addfile(info, io.BytesIO(payload))
    if failure == "invalid-archive":
        archive.write_bytes(b"not a tar archive")
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    if failure == "checksum":
        archive.write_bytes(b"tampered archive")
    filename = next(
        line.split()[1]
        for line in _RUNTIME_HASHES.read_text().splitlines()
        if not line.startswith("#") and f"-{triple}-" in line
    )
    (tmp_path / "python-runtimes.sha256").write_text(
        "" if failure == "missing-checksum" else f"{checksum}  {filename}\n"
    )
    driver = r"""
set -eu
log_info() { :; }
log_success() { :; }
log_error() { echo "$*" >&2; }
curl() {
    printf '%s\n' "$4" > "$PYTHON_TEST_URL"
    [ "$PYTHON_TEST_FAILURE" != download ] || return 22
    cp "$PYTHON_TEST_ARCHIVE" "$3"
}
tar() {
    touch "$PYTHON_TEST_EXTRACTED"
    command tar "$@"
}
"""
    destination = tmp_path / "installer"
    destination.mkdir()
    result = subprocess.run(
        [
            "bash",
            "-c",
            driver
            + _shell_function("download_installer_python")
            + '\ndownload_installer_python "$1" "$2"\n',
            "test",
            str(destination),
            triple,
        ],
        env={
            **os.environ,
            "SCRIPT_DIR": str(tmp_path),
            "PYTHON_TEST_ARCHIVE": str(archive),
            "PYTHON_TEST_URL": str(tmp_path / "url"),
            "PYTHON_TEST_EXTRACTED": str(tmp_path / "extracted"),
            "PYTHON_TEST_FAILURE": failure,
        },
        capture_output=True,
        text=True,
    )
    return result, destination


@pytest.mark.parametrize(
    "triple",
    [
        "x86_64-unknown-linux-gnu",
        "aarch64-unknown-linux-gnu",
        "x86_64-apple-darwin",
        "aarch64-apple-darwin",
    ],
)
@pytest.mark.parametrize("corrupt", [False, True])
def test_python_checksum_is_verified_before_extraction(
    tmp_path: Path, triple: str, corrupt: bool
) -> None:
    result, destination = _download_python(tmp_path, triple, "checksum" if corrupt else "")
    assert (tmp_path / "url").read_text().strip() == (
        "https://github.com/astral-sh/python-build-standalone/releases/download/20260320/"
        f"cpython-3.13.12+20260320-{triple}-install_only_stripped.tar.gz"
    )
    if corrupt:
        assert result.returncode != 0
        assert "Python checksum verification failed" in result.stderr
        assert not (tmp_path / "extracted").exists()
        assert not (destination / "python" / triple).exists()
    else:
        assert result.returncode == 0, result.stderr
        binary = destination / "python" / triple / "python/bin/python3"
        assert binary.read_bytes() == b"#!/bin/sh\nexit 0\n"
        assert os.access(binary, os.X_OK)
    assert not list(destination.glob(".python-*"))


@pytest.mark.parametrize(
    "failure", ["missing-checksum", "download", "invalid-archive", "missing-binary"]
)
def test_python_download_fails_closed(tmp_path: Path, failure: str) -> None:
    triple = "x86_64-unknown-linux-gnu"
    result, destination = _download_python(tmp_path, triple, failure)
    assert result.returncode != 0
    assert not (destination / "python" / triple).exists()
    assert not list(destination.glob(".python-*"))
    if failure in {"missing-checksum", "download"}:
        assert not (tmp_path / "extracted").exists()
    if failure == "missing-checksum":
        assert not (tmp_path / "url").exists()


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
