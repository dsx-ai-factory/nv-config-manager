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
"""Exercise resource publication without reaching a registry or exposing secrets."""

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / ".gitlab/ci/scripts/publish_installer_resource.sh"


@pytest.mark.parametrize(
    "failure",
    [
        "",
        "new-resource",
        "checksum",
        "existing",
        "missing-token",
        "lookup-401",
        "lookup-403",
        "lookup-500",
        "lookup-network",
        "reserve-401",
        "reserve-500",
        "reserve-network",
    ],
)
def test_publication_checks_inputs_before_upload(tmp_path: Path, failure: str) -> None:
    archive = tmp_path / "nvcm-installer-1.2.3.tar.gz"
    archive.write_bytes(b"offline distribution")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_name(archive.name + ".sha256").write_text(
        f"{'0' * 64 if failure == 'checksum' else digest}  {archive.name}\n"
    )
    calls = tmp_path / "calls"
    ngc = tmp_path / "ngc"
    ngc.write_text("""#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> "$CALLS"
if [[ "$*" == *'upload-version'* ]]; then
    [[ "$NGC_CLI_API_KEY" == test-secret ]]
    [[ -f "$5/nvcm-installer-1.2.3.tar.gz" ]]
    [[ -f "$5/nvcm-installer-1.2.3.tar.gz.sha256" ]]
    [[ ! -e "$5/auth-header" ]]
fi
""")
    ngc.chmod(0o755)
    curl = tmp_path / "curl"
    curl.write_text("""#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> "$CALLS"
if [[ "$*" == *'--request POST'* ]]; then
    [[ "$*" == *'{"versionId":"1.2.3"}'* ]]
    case "$FAILURE" in
        existing) printf 409 ;;
        reserve-network) exit 7 ;;
        reserve-*) printf '%s' "${FAILURE#reserve-}" ;;
        *) printf 201 ;;
    esac
else
    case "$FAILURE" in
        new-resource) printf 404 ;;
        lookup-network) exit 7 ;;
        lookup-*) printf '%s' "${FAILURE#lookup-}" ;;
        *) printf 200 ;;
    esac
fi
""")
    curl.chmod(0o755)
    result = subprocess.run(
        ["bash", str(SCRIPT), "1.2.3", str(tmp_path)],
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "CALLS": str(calls),
            "FAILURE": failure,
            "NVCM_INSTALLER_NGC_PATH": "example/team",
            "NVCM_INSTALLER_NGC_TOKEN_VAR": "RESOURCE_TOKEN",
            "RESOURCE_TOKEN": "" if failure == "missing-token" else "test-secret",
        },
        capture_output=True,
        text=True,
    )
    log = calls.read_text() if calls.exists() else ""
    assert "test-secret" not in result.stdout + result.stderr + log
    if failure not in {"", "new-resource"}:
        assert result.returncode != 0
        assert "upload-version" not in log
    else:
        assert result.returncode == 0, result.stderr
        assert "upload-version" in log
        assert ("resource create" in log) == (failure == "new-resource")
